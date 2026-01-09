import base64
import logging
from functools import reduce, partial
# from urllib.parse import quote

import requests
import yaml

from django.conf import settings
from django.shortcuts import redirect, render
from django.contrib.sites.shortcuts import get_current_site
from django.utils.translation import gettext_lazy as _

from rdmo.options.models import OptionSet, Option
from rdmo.domain.models import Attribute
from rdmo.projects.models.value import Value
from rdmo.projects.models.project import Project
from rdmo.questions.models import Catalog
from rdmo.core.imports import handle_fetched_file
from rdmo.projects.imports import RDMOXMLImport
from rdmo.projects.mixins import ProjectImportMixin
from rdmo.projects.utils import save_import_snapshot_values, save_import_tasks, save_import_values, save_import_views
from rdmo.projects.serializers.export import ProjectSerializer as ProjectExportSerializer
from rdmo.core.plugins import get_plugin

from ..mixins import GitHubProviderMixin
from ..forms.forms import GitHubImportForm

logger = logging.getLogger(__name__)

APP_TYPE = settings.GITHUB_PROVIDER['app_type']

class GitHubImportProvider(GitHubProviderMixin, ProjectImportMixin, RDMOXMLImport):
    @property
    def import_choices(self):
        import_choices =[
            ('False,data/smp.xml', (_('RDMO XML'), 'xml')),
            ('False,CITATION.cff', ('CITATION', 'citation')),
            ('False,LICENSE', ('LICENSE', 'license')),
            ('False,blabla', (_('repo dependency graph'), 'sbom')),
            ('False,blabla', (_('repo languages'), 'languages'))
        ]

        return import_choices

    def render(self):
        redirect_url = self.request.build_absolute_uri()
        self.process_app_context(self.request, redirect_url=redirect_url)
        
        access_token = self.validate_access_token(self.request, self.get_from_session(self.request, 'access_token'))
        if access_token is None:
            return self.authorize(self.request)
        
        context = {
            'source_title': 'GitHub',
            'app_type': APP_TYPE,
            'repo_display': 'block',
            'other_repo_display': 'none',
            'form': self.get_form(self.request, GitHubImportForm, import_choices=self.import_choices)
        }
        return render(self.request, 'plugins/github_import_form.html', context, status=200)

    def submit(self):
        form = self.get_form(self.request, GitHubImportForm, self.request.POST, import_choices=self.import_choices)

        if 'cancel' in self.request.POST:
            if self.project is None:
                return redirect('projects')
            else:
                return redirect('project', self.project.id)

        if form.is_valid():            
            self.request.session['import_source_title'] = self.source_title = 'GitHub'

            urls = self.process_form_data(form.cleaned_data)
            repo_url = urls.pop('repo')

            # self.store_in_session(self.request, 'project_id', self.current_project.id)
            self.store_in_session(self.request, 'request_urls', urls)
            
            return self.make_request(self.request, 'get', repo_url)

        other_repo_check = True if 'other_repo_check' in form.data else False
        repo_display = 'none' if other_repo_check else 'block'
        other_repo_display = 'block' if other_repo_check else 'none'
        context = {
            'source_title': 'GitHub',
            'app_type': APP_TYPE,
            'repo_display': repo_display,
            'other_repo_display': other_repo_display,
            'form': form
        }
        return render(self.request, 'plugins/github_import_form.html', context, status=200)
    
    def process_form_data(self, form_data):
        other_repo_check  = form_data['other_repo_check']
        if other_repo_check:
            repo = form_data['other_repo']
        else:
            repo = form_data['repo']

        imports = {i.split(',')[0]: i.split(',')[1] for i in form_data['imports']}

        urls = {
            'repo': self.get_request_url(repo),
            'sbom': self.get_request_url(repo, suffix='/dependency-graph/sbom'), # only in default branch
            'languages': self.get_request_url(repo, suffix='/languages'), # only in default branch
            # 'contents': self.get_request_url(repo, suffix='/contents')
            'xml': self.get_request_url(repo, path=imports['xml'], ref=form_data['ref']) if 'xml' in imports else None,
            'citation': self.get_request_url(repo, path=imports['citation'], ref=form_data['ref']) if 'citation' in imports else None,
            'license': self.get_request_url(repo, path=imports['license'], ref=form_data['ref']) if 'license' in imports else None,
        }

        selected_urls = {k:urls.get(k) for k in ['repo', *imports.keys()]}
        return selected_urls
    
    def groupby_values(self, initial, v, groupby):
        groupby_mapping = {
            'attribute': v.attribute.uri,
            'option': v.option.uri if v.option is not None else None,
            'text': v.text.lower(),
            'set_index': v.set_index,
            'set_prefix': v.set_prefix
        }
        _groupby = str(groupby_mapping[groupby])
        if _groupby not in initial.keys():
            initial[_groupby] = [v]
        else:
            initial[_groupby].append(v)
        return initial

    def merge_licenses(self, new_license_values, import_values):
        existing_license_option_uris = [v.option.uri for v in import_values if v.attribute.uri == 'https://rdmorganiser.github.io/terms/domain/smp/software-license']
        
        grouped_new_license_values = reduce(partial(self.groupby_values, groupby='option'), new_license_values, {}) 
        unique_new_license_values = [value_list[0] for value_list in grouped_new_license_values.values()]
        for v in unique_new_license_values:
            license_option_uri =v.option.uri
            if (
                len(existing_license_option_uris) == 0 or
                license_option_uri not in existing_license_option_uris
            ):
                import_values.append(v)

        return import_values

    def get_repo_license(self, url, import_values, response=None, license_id=None):
        # 1. Get license option
        license_options = OptionSet.objects.get(uri='https://rdmorganiser.github.io/terms/options/software-license').elements

        license_dict = response.json().get('license') if response is not None else {}
        if isinstance(license_dict, dict):
            license_id = license_dict.get('spdx_id') if license_id is None else license_id
            collection_index, license_option = next(
                ((i, o) for i, o in enumerate(license_options) if o.text == license_id), 
                (None, None)
            )
            if license_option is None:
                collection_index, license_option = next(
                    (i, o) for i, o in enumerate(license_options) if o.uri == 'https://rdmorganiser.github.io/terms/options/software-license/other-license'
                )

        else:
            return import_values
        
        # 2. Create license value with correct option
        # Only append unique new license values
        license_text = license_id if license_option.uri == 'https://rdmorganiser.github.io/terms/options/software-license/other-license' else ''
        value = Value()
        value.attribute = Attribute.objects.get(uri='https://rdmorganiser.github.io/terms/domain/smp/software-license')
        value.set_collection = False
        value.collection_index = collection_index
        value.text = license_text
        value.option = license_option

        import_values = self.merge_licenses([value], import_values)
                
        return import_values
    
    def get_identifier_option(self, identifier_type):
        options = OptionSet.objects.get(uri='https://rdmorganiser.github.io/terms/options/software_identifier').elements

        collection_index, option = next(
            ((i, o) for i, o in enumerate(options) if o.uri.endswith(identifier_type)), 
            (None, None)
        )
        
        return collection_index, option

    def merge_languages(self, new_language_values, import_values):
        existing_languages = [v.text.lower() for v in import_values if v.attribute.uri == 'https://rdmorganiser.github.io/terms/domain/smp/language']
        
        grouped_new_language_values = reduce(partial(self.groupby_values, groupby='text'), new_language_values, {}) 
        unique_new_language_values = [value_list[0] for value_list in grouped_new_language_values.values()]
        for i, v in enumerate(unique_new_language_values):
            language = v.text.lower()
            if (len(existing_languages) == 0 or language not in existing_languages):
                v.collection_index = i + len(existing_languages)
                import_values.append(v)

        return import_values
    
    def get_repo_languages(self, url, import_values):
        response = requests.get(url)
        languages = []
        try:
            response.raise_for_status()
            languages = response.json().keys()
        except:
            pass

        new_language_values = []
        for language in languages:            
            value = Value()
            value.attribute = Attribute.objects.get(uri='https://rdmorganiser.github.io/terms/domain/smp/language')
            value.set_collection = False
            value.text = language
            new_language_values.append(value)
        
        import_values = self.merge_languages(new_language_values, import_values)

        return import_values
    
    def merge_dependencies(self, new_dependencies_values, import_values):
        existing_dependencies_value_list = [
            (i, v) for i, v in enumerate(import_values) 
            if v.attribute.uri == 'https://rdmorganiser.github.io/terms/domain/smp/external-components'
        ]

        # Only one value for dependencies, so if there are multiple, merge all their texts and append only one to import_values
        grouped_new_dependencies_values = reduce(partial(self.groupby_values, groupby='text'), new_dependencies_values, {}) 
        unique_new_dependencies_texts = [value_list[0].text for value_list in grouped_new_dependencies_values.values()]
        new_dependencies_text = '\n'.join(unique_new_dependencies_texts)
        
        # if no value for dependencies in import_values, then append the first new value with all dependency texts (if many)
        if len(existing_dependencies_value_list) == 0 and len(new_dependencies_values) > 0:
            new_value = new_dependencies_values[0]
            new_value.text = new_dependencies_text
            print(f'    appending new_value: {new_value.text}')
            import_values.append(new_value)
        elif len(existing_dependencies_value_list) > 0:
            dependencies_value_index, dependencies_value = existing_dependencies_value_list[0]
            new_value_text = (
                dependencies_value.text + new_dependencies_text
                if dependencies_value.text.endswith('\n') 
                else f'{dependencies_value.text}\n{new_dependencies_text}'
            )
            dependencies_value.text = new_value_text

            # There is only one dependencies value per project, delete duplicates if they exist
            duplicated_dependencies_value_indices = [i for (i, v) in existing_dependencies_value_list if i != dependencies_value_index]
            if len(duplicated_dependencies_value_indices) > 0:
                for i in duplicated_dependencies_value_indices:
                    import_values.pop(i)

        return import_values

    def merge_dependency_licenses(self, new_dependency_licenses_values, import_values):
        existing_dependency_licenses_value_list = [
            (i, v) for i, v in enumerate(import_values) 
            if v.attribute.uri == 'https://rdmorganiser.github.io/terms/domain/smp/third-party-licenses'
        ]

        # Only one value for dependency licenses, so if there are multiple, merge all their texts and append only one to import_values
        grouped_new_dependency_licenses_values = reduce(partial(self.groupby_values, groupby='text'), new_dependency_licenses_values, {}) 
        unique_new_dependency_licenses_texts = [value_list[0].text for value_list in grouped_new_dependency_licenses_values.values()]
        new_dependency_licenses_text = '\n'.join(unique_new_dependency_licenses_texts)
        
        # if no value for dependency licenses in import_values, then append the first new value with all dependency license texts (if many)
        if len(existing_dependency_licenses_value_list) == 0 and len(new_dependency_licenses_values) > 0:
            new_value = new_dependency_licenses_values[0]
            new_value.text = new_dependency_licenses_text
            import_values.append(new_value)
        elif len(existing_dependency_licenses_value_list) > 0:
            dependency_licenses_value_index, dependency_licenses_value = existing_dependency_licenses_value_list[0]
            new_value_text = (
                dependency_licenses_value.text + new_dependency_licenses_text
                if dependency_licenses_value.text.endswith('\n') 
                else f'{dependency_licenses_value.text}\n{new_dependency_licenses_text}'
            )
            dependency_licenses_value.text = new_value_text

            # There is only one dependency licenses value per project, delete duplicates if they exist
            duplicated_dependency_licenses_value_indices = [i for (i, v) in existing_dependency_licenses_value_list if i != dependency_licenses_value_index]
            if len(duplicated_dependency_licenses_value_indices) > 0:
                for i in duplicated_dependency_licenses_value_indices:
                    import_values.pop(i)
            
            print('DEPENDENCY LICENSES VALUE')
            print(f'    current text: {import_values[dependency_licenses_value_index].text}')
            print(f'    new text: {dependency_licenses_value.text}')
            # import_values[dependencies_value_index] = dependencies_value

        return import_values
    
    def get_repo_dependencies(self, url, import_values):
        response = requests.get(url)
        try:
            response.raise_for_status()
            sbom = response.json().get('sbom')
        except:
            return import_values
        
        dependencies_str = ''
        dependency_licenses = {}
        repo_package_name = sbom.get('name')
        for d in sbom.get('packages', []):
            name = d.get('name')
            if name == repo_package_name: # repo itself is listed as package
                continue
            # dependencies_str += f'{name} {version}\n'
            dependencies_str += f'{name}\n'

            # version = d.get('versionInfo')
            license = d.get('licenseConcluded') if 'licenseConcluded' in d else (
                d.get('licenseDeclared') if 'licenseDeclared' in d else None
            )
            if isinstance(license, str):
                license = license.split(' AND')
                # license = "license_1, license_2 and license_3" | "license_1 and license_2" | license_1
                license = ','.join(license[:-1]) + _(' and') + license[-1] if len(license) > 1 else license[0] 
            
            if license is not None and license in dependency_licenses:
                dependency_licenses[license].append(name)
            elif license is not None and license not in dependency_licenses:
                dependency_licenses[license] = [name]
        
        if len(dependencies_str) > 0:
            dependencies_value = Value()
            dependencies_value.attribute = Attribute.objects.get(uri='https://rdmorganiser.github.io/terms/domain/smp/external-components')
            dependencies_value.set_collection = False
            dependencies_value.text = dependencies_str

            import_values = self.merge_dependencies([dependencies_value], import_values)

        if len(dependency_licenses) > 0:
            dependency_licenses_str = ''
            for k, v in dependency_licenses.items():
                dependency_licenses_str += f'{k} ({", ".join(v)})\n'
            
            dependency_licenses_value = Value()
            dependency_licenses_value.attribute = Attribute.objects.get(uri='https://rdmorganiser.github.io/terms/domain/smp/third-party-licenses')
            dependency_licenses_value.set_collection = False
            dependency_licenses_value.text = dependency_licenses_str

            import_values = self.merge_dependency_licenses([dependency_licenses_value], import_values)

        if len(dependencies_str) > 0 or len(dependency_licenses) > 0:
            application_class_value = Value()
            application_class_value.attribute = Attribute.objects.get(uri='https://rdmorganiser.github.io/terms/domain/smp/application-class')
            application_class_value.set_collection = False
            application_class_value.option = (
                Option.objects.get(uri='https://rdmorganiser.github.io/terms/options/application-class/2')
                if len(dependency_licenses) > 0 
                else Option.objects.get(uri='https://rdmorganiser.github.io/terms/options/application-class/1')
            )
            
            import_values = self.merge_application_class([application_class_value], import_values)

        return import_values

    def merge_title(self, new_title_values, import_values):
        for v in import_values:
            try:
                print(f'v.attribute.uri: {v.attribute.uri}')
            except:
                pass
            try:
                print(f'v.text: {v.text}')
            except:
                pass
            try:
                print(f'v.option.uri: {v.option.uri}')
            except:
                pass
        existing_title_value_list = [v for v in import_values if v.attribute.uri == 'https://rdmorganiser.github.io/terms/domain/project/title']

        if len(existing_title_value_list) == 0:
            import_values.append(new_title_values[0])

        return import_values
    
    def merge_authors(self, new_author_values, import_values):
        existing_set_id_values = [v for v in import_values if v.attribute.uri == 'https://rdmorganiser.github.io/terms/domain/project/partner/id']
        existing_authors_orcids = [v.text for v in import_values if v.attribute.uri == 'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/orcid']
        
        employment_attributes = [
            'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/role',
            'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/affiliation',
            'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/affiliation/ror-autocomplete',
            'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/affiliation/ror-id'
        ]

        grouped_new_author_values = reduce(
            partial(self.groupby_values, groupby='set_index'), 
            [v for v in new_author_values if v.attribute.uri not in employment_attributes], 
            {}
        )
        grouped_new_author_values = reduce(
            partial(self.groupby_values, groupby='set_prefix'), 
            [v for v in new_author_values if v.attribute.uri in employment_attributes], 
            grouped_new_author_values
        )

        new_values = []
        for i, author_values_list in enumerate(grouped_new_author_values.values()):
            author_orcid = next(
                (v.text for v in author_values_list if v.attribute.uri == 'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/orcid'), 
                None
            )            
            if author_orcid in existing_authors_orcids:
                print(f'    author already in import_values -> continuing')
                continue

            for v in author_values_list:
                attr_uri = v.attribute.uri
                
                if attr_uri not in employment_attributes:
                    v.set_index = i + len(existing_set_id_values)

                if attr_uri in employment_attributes:
                    v.set_prefix = str(i + len(existing_set_id_values)) # set_prefix is a string field

                new_values.append(v)

        def sort_by_external_id(e):
            # values without an external id come first
            # external id marks values used by option providers
            return e.external_id

        new_values.sort(key = sort_by_external_id)
        import_values.extend(new_values)
        merged_new_authors = True if len(new_values) > 0 else False
        
        return import_values, merged_new_authors

    def merge_identifiers(self, new_identifier_values, import_values):
        existing_identifier_option_uris = [v.option.uri for v in import_values if v.attribute.uri == 'https://rdmorganiser.github.io/terms/domain/smp/software-pid']
        
        grouped_new_identifier_values = reduce(partial(self.groupby_values, groupby='option'), new_identifier_values, {}) 
        unique_new_identifier_values = [value_list[0] for value_list in grouped_new_identifier_values.values()]
        
        new_values = []
        for v in unique_new_identifier_values:
            identifier_option_uri =v.option.uri
            if (
                len(existing_identifier_option_uris) == 0 or
                identifier_option_uri not in existing_identifier_option_uris
            ):
                new_values.append(v)

        import_values.extend(new_values)
        merged_new_identifiers = True if len(new_values) > 0 else False
        
        return import_values, merged_new_identifiers

    def merge_application_class(self, new_application_class_values, import_values):
        existing_application_class_value_list = [
            (i, v) for i, v in enumerate(import_values) 
            if v.attribute.uri == 'https://rdmorganiser.github.io/terms/domain/smp/application-class'
        ]

        application_class_values = [
            v for v in [*new_application_class_values, *import_values] 
            if v.attribute.uri == 'https://rdmorganiser.github.io/terms/domain/smp/application-class'
        ]
        highest_class_option = max([int(v.option.uri.split('/')[-1]) for v in application_class_values])

        application_class_v = next(v for v in application_class_values if int(v.option.uri.split('/')[-1]) == highest_class_option)

        if len(existing_application_class_value_list) == 0:
            import_values.append(application_class_v)
        else:
            application_class_value_index, application_class_value = existing_application_class_value_list[0]
            application_class_value.option = application_class_v.option

            duplicated_application_class_value_indices = [i for (i, v) in existing_application_class_value_list if i != application_class_value_index]
            if len(duplicated_application_class_value_indices) > 0:
                for i in duplicated_application_class_value_indices:
                    import_values.pop(i)

        return import_values
        
    def get_cff_title(self, cff_data, import_values):
        title_value = Value()
        title_value.attribute = Attribute.objects.get(uri='https://rdmorganiser.github.io/terms/domain/project/title')
        title_value.set_collection = False
        title_value.text = cff_data.get('title')
        import_values = self.merge_title([title_value], import_values)

        return import_values
    
    def get_cff_license(self, cff_data, import_values, url):
        for _id in cff_data.get('license'):
            import_values = self.get_repo_license(url, import_values, license_id=_id)

        return import_values
    
    def get_cff_authors(self, cff_data, import_values):
        author_values = []
        attributes = {
            'family-names': 'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/family-name',
            'given-names': 'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/given-name',
            'name': 'https://rdmorganiser.github.io/terms/domain/project/partner/name',
            'orcid': 'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/orcid',
            'website': 'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/website',
            'affiliation': 'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/affiliation'
        }
        for i, author in enumerate(cff_data.get('authors', [])):
            type = (
                'person' 
                if (author.get('given-names', None) is not None or author.get('family-names', None) is not None)
                else 'entity'
            )
            set_type_value = Value()
            set_type_value.attribute = Attribute.objects.get(uri='https://rdmo.mpdl.mpg.de/terms/domain/project/partner/type')
            set_type_value.set_index = i
            set_type_value.set_collection = True
            option_uri = (
                'https://rdmo.mpdl.mpg.de/terms/options/partner-types/person'
                if type == 'person'
                else 'https://rdmo.mpdl.mpg.de/terms/options/partner-types/entity'
            )
            set_type_value.option = Option.objects.get(uri=option_uri)            
            author_values.append(set_type_value)


            set_label = (
                f'{author.get("given-names", "")} {author.get("family-names", "")}'
                if type == 'person'
                else author.get('name', '')
            )
            set_id_value = Value()
            set_id_value.attribute = Attribute.objects.get(uri='https://rdmorganiser.github.io/terms/domain/project/partner/id')
            set_id_value.set_index = i
            set_id_value.set_collection = True
            set_id_value.text = set_label            
            author_values.append(set_id_value)
            
            for k, v in author.items():
                # no SMP field for name or orcid for an author of type person, but possible by cff schema
                if (
                    ((k == 'name' or k == 'website') and type == 'person') or
                    (k == 'orcid' and type == 'entity')
                ):
                    continue

                if k in attributes and k != 'affiliation':
                    author_value = Value()
                    author_value.attribute = Attribute.objects.get(uri=attributes.get(k))
                    author_value.set_index = i
                    author_value.set_collection = True
                    author_value.text = v
                    author_values.append(author_value)
                
                elif k == 'affiliation':
                    cff_a_str = v
                    affiliations = cff_a_str.split(' & ')
                    for j, a in enumerate(affiliations):
                        affiliation_value = Value()
                        affiliation_value.attribute = Attribute.objects.get(uri=attributes.get(k))
                        affiliation_value.set_prefix = str(i) # set_prefix is a string field
                        affiliation_value.set_index = j
                        affiliation_value.set_collection = True
                        affiliation_value.text = a
                        author_values.append(affiliation_value)

        found_new_authors = False
        if len(author_values) > 0:
            import_values, found_new_authors = self.merge_authors(author_values, import_values)

        return import_values, found_new_authors
    
    def get_cff_identifiers(self, cff_data, import_values):
        _identifiers = []
        _identifier_types = []
        if 'identifiers' in cff_data:
            _identifiers.extend(cff_data.get('identifiers'))
            _identifier_types.extend([i.get('type') for i in cff_data.get('identifiers', [])])
        if 'doi' in cff_data and 'doi' not in _identifier_types:
            _identifiers.append({'type': 'doi', 'value': cff_data.get('doi')})
        if 'url' in cff_data and 'url' not in _identifier_types:
            _identifiers.append({'type': 'url', 'value': cff_data.get('url')})

        identifier_values = []
        for identifier in _identifiers:
            identifier_type = identifier.get('type')
            collection_index, identifier_option = self.get_identifier_option(identifier_type)
            
            identifier_value = Value()
            identifier_value.attribute = Attribute.objects.get(uri='https://rdmorganiser.github.io/terms/domain/smp/software-pid')
            identifier_value.set_collection = False
            identifier_value.collection_index = collection_index
            identifier_value.text = identifier.get('value')
            identifier_value.option = identifier_option
            identifier_values.append(identifier_value)

        found_new_identifiers = False
        if len(identifier_values) > 0:
            import_values, found_new_identifiers = self.merge_identifiers(identifier_values, import_values)

        return import_values, found_new_identifiers
    
    def get_repo_citation_file(self, url, import_values):
        # https://github.com/citation-file-format/citation-file-format/blob/main/schema-guide.md
        cff_data = {}
        response = requests.get(url)
        try:
            response.raise_for_status()
            encoded_content = response.json().get('content')
            decoded_bytes = base64.b64decode(encoded_content)
            content = decoded_bytes.decode('utf-8')
            cff_data = yaml.safe_load(content)
        except:
            pass
        
        if 'title' in cff_data:
            import_values = self.get_cff_title(cff_data, import_values)

        if 'license' in cff_data:
            import_values = self.get_cff_license(cff_data, import_values, url)

        found_new_authors = False
        if 'authors' in cff_data:
            import_values, found_new_authors = self.get_cff_authors(cff_data, import_values)

        found_new_identifiers = False
        if 'identifiers' in cff_data or 'doi' in cff_data or 'url' in cff_data:
            import_values, found_new_identifiers = self.get_cff_identifiers(cff_data, import_values)
        
        if found_new_authors or found_new_identifiers:
            application_class_value = Value()
            application_class_value.attribute = Attribute.objects.get(uri='https://rdmorganiser.github.io/terms/domain/smp/application-class')
            application_class_value.set_collection = False
            application_class_value.option = (
                Option.objects.get(uri='https://rdmorganiser.github.io/terms/options/application-class/2')
                if found_new_authors
                else Option.objects.get(uri='https://rdmorganiser.github.io/terms/options/application-class/1')
            )
            import_values = self.merge_application_class([application_class_value], import_values)

        return import_values

    def merge_xml_values(self, uri, new_xml_values, import_values):
        merge_mapping = {
            'https://rdmorganiser.github.io/terms/domain/smp/software-license': self.merge_licenses,
            'https://rdmorganiser.github.io/terms/domain/smp/language': self.merge_languages,
            'https://rdmorganiser.github.io/terms/domain/smp/external-components': self.merge_dependencies,
            'https://rdmorganiser.github.io/terms/domain/smp/third-party-licenses': self.merge_dependency_licenses,
            'https://rdmorganiser.github.io/terms/domain/project/title': self.merge_title,
            'https://rdmorganiser.github.io/terms/domain/smp/software-pid': self.merge_identifiers,
            'https://rdmorganiser.github.io/terms/domain/smp/application-class': self.merge_application_class,
            'authors': self.merge_authors
        }

        if uri in merge_mapping.keys():
            merging_function = merge_mapping.get(uri)
            merged_result = merging_function(new_xml_values, import_values)

            # some merge_identifiers and merge_authors return a tuple
            if isinstance(merged_result, tuple):
                import_values = merged_result[0]
            else:
                import_values = merged_result
        
        return import_values
           
    def get_xml_values(self, url, import_values, xml_import_plugin):
        # if xml values have old attributes that do not exist in catalog anymore v.attribute == None
        new_values = [v for v in xml_import_plugin.values if v.attribute is not None]
        xml_import_plugin.values = new_values
        
        if len(import_values) == 0:
            return xml_import_plugin.values
        
        author_attribute_uris = [
            'https://rdmorganiser.github.io/terms/domain/project/partner/id',
            'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/family-name',
            'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/given-name',
            'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/name',
            'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/orcid',
            'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/orcid-autocomplete',
            'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/role',
            'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/affiliation',
            'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/affiliation/ror-autocomplete',
            'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/affiliation/ror-id'
        ]
        grouped_import_values = reduce(
            partial(self.groupby_values, groupby='attribute'), 
            [v for v in import_values if v.attribute.uri not in author_attribute_uris], 
            {}
        )
        grouped_import_values['authors'] = [v for v in import_values if v.attribute.uri in author_attribute_uris]
        
        grouped_xml_values = reduce(
            partial(self.groupby_values, groupby='attribute'), 
            [v for v in xml_import_plugin.values if v.attribute.uri not in author_attribute_uris], 
            {}
        )
        grouped_xml_values['authors'] = [v for v in xml_import_plugin.values if v.attribute.uri in author_attribute_uris]

        for uri, xml_values_list in grouped_xml_values.items():
            # print(f'uri: {uri}')
            if uri not in grouped_import_values.keys():
                # print(f'    uri not yet in grouped_import_values')
                import_values.extend(xml_values_list)
            else:
                # print(f'    uri already in grouped_import_values -> merging')
                import_values = self.merge_xml_values(uri, xml_values_list, import_values)

        return import_values
    
    def get_import_values(self, repo_response, xml_import_plugin, request_urls):
        '''Return a list with Value() instances to create an xml with all the info
        from the selected repository.
        
        If the user fills out the path to an RDMO xml file (optional), its Value() instances
        will have precedence over information in the repository.
        '''
        
        functions = {
            'sbom': {
                'function': self.get_repo_dependencies,
                'function_kwargs': {}
            },
            'languages': {
                'function': self.get_repo_languages,
                'function_kwargs': {}
            },
            'citation': {
                'function': self.get_repo_citation_file,
                'function_kwargs': {}
            },
            'license': {
                'function': self.get_repo_license,
                'function_kwargs': {
                    'response': repo_response 
                }
            },
            'xml': {
                'function': self.get_xml_values,
                'function_kwargs': {
                    'xml_import_plugin': xml_import_plugin
                }
            }
        }

        import_values = []
        for k, url in request_urls.items():
            f, f_kwargs = functions[k].values()
            import_values = f(url, import_values, **f_kwargs)
            
        return import_values

    def get_import_project(self, repo_response, request_urls):
        '''Return a Project() instance that will be the basis to create an xml with all the info
        from the selected repository. 
        
        If the user fills out the path to an RDMO xml file (optional), the Project() instance
        will have its information. If no file path is filled out, the Project() instance will be created from scratch.
        '''

        catalog = self.current_project.catalog if self.current_project else Catalog.objects.get(uri='https://rdmorganiser.github.io/terms/questions/smp')
        title = (
            repo_response.json().get('html_url').split('/')[-1] 
            if repo_response.json().get("html_url") is not None
            else _('GitHub Import')
        )
        import_project = Project(
            catalog=catalog,
            title=title
        )
        xml_import_plugin = None
        
        if 'xml' in request_urls:
            xml_response = requests.get(request_urls['xml'])

            try:
                xml_response.raise_for_status()
                xml_content = handle_fetched_file(base64.b64decode(xml_response.json().get('content')))
                xml_import_plugin = self.get_import_plugin('xml', self.current_project)
                xml_import_plugin.file_name = xml_content

                if xml_import_plugin.check():
                    try:
                        # extract all Value() instances found in xml file
                        xml_import_plugin.process()
                        import_project = (
                            xml_import_plugin.project 
                            if xml_import_plugin.project is not None 
                            else Project( # new Project() because xml_import_plugin.project is None
                                catalog=xml_import_plugin.catalog, # xml_import_plugin.catalog == self.current_project.catalog
                                title='bla' # does not matter since updating existing project
                            )
                        )
                    except:
                        pass
            
            except:
                pass

        return import_project, xml_import_plugin
    
    def create_import_xml_file(self, request, import_project, import_values, xml_import_plugin, request_urls):
        # 1. If Value() for title (title_value) exists and title_value != import_project.title, update import_project.title if first import source was xml
        title = next((v.text for v in import_values if v.attribute.uri == 'https://rdmorganiser.github.io/terms/domain/project/title'), None)
        first_import_source = list(request_urls.keys())[0]
        if title is not None and title != import_project.title and first_import_source != 'xml':
            import_project.title = title

        checked = [
            f'{v.attribute.uri}[{v.set_prefix}][{v.set_index}][{v.collection_index}]'
            for v in import_values
        ]

        snapshots = xml_import_plugin.snapshots if xml_import_plugin is not None else []
        self.update_values(None, import_project.catalog, import_values, snapshots)

        import_project.site = get_current_site(request)
        import_project.save()

        tasks = xml_import_plugin.tasks if xml_import_plugin is not None else []
        views = xml_import_plugin.views if xml_import_plugin is not None else []
        save_import_values(import_project, import_values, checked)
        save_import_snapshot_values(import_project, snapshots, checked)
        save_import_tasks(import_project, tasks)
        save_import_views(import_project, views)
        
        xml_export_plugin = get_plugin('PROJECT_EXPORTS', 'xml')
        xml_export_plugin.project = import_project
        xml_response = xml_export_plugin.render()
        
        # Value.objects.filter(project=import_project).delete()
        Project.objects.filter(pk=import_project.id).delete()

        return xml_response

    def get_success(self, request, response):
        request_urls = self.pop_from_session(self.request, 'request_urls')

        # 1. Create (or extract from repo xml file) Project() instance
        import_project, xml_import_plugin = self.get_import_project(response, request_urls)

        # 2. Create Value() instances for all import data found in repo (and repo xml file if exists)
        import_values = self.get_import_values(response, xml_import_plugin, request_urls)
        
        # 3. Create xml file with all info from repo (and from xml file in repo if exists)
        xml_response = self.create_import_xml_file(request, import_project, import_values, xml_import_plugin, request_urls)

        # 4. Pass newly created xml file with all repo imports to ProjectUpdateImportView or ProjectCreateImportView
        request.session['import_file_name'] = handle_fetched_file(xml_response.content)

        if self.current_project:
            return redirect('project_update_import', self.current_project.id)
        else:
            return redirect('project_create_import')

class GitHubImport(GitHubProviderMixin, RDMOXMLImport):

    def render(self):
        redirect_url = self.request.build_absolute_uri()
        self.process_app_context(self.request, redirect_url=redirect_url)
        
        access_token = self.validate_access_token(self.request, self.get_from_session(self.request, 'access_token'))
        if access_token is None:
            return self.authorize(self.request)
        
        context = {
            'source_title': 'GitHub',
            'app_type': APP_TYPE,
            'repo_display': 'block',
            'other_repo_display': 'none',
            'form': self.get_form(self.request, GitHubImportForm)
        }
        return render(self.request, 'plugins/github_import_form.html', context, status=200)

    def submit(self):
        form = self.get_form(self.request, GitHubImportForm, self.request.POST)

        if 'cancel' in self.request.POST:
            if self.project is None:
                return redirect('projects')
            else:
                return redirect('project', self.project.id)

        if form.is_valid():            
            self.request.session['import_source_title'] = self.source_title = form.cleaned_data['path']

            url = self.process_form_data(form.cleaned_data)
            return self.make_request(self.request, 'get', url)

        other_repo_check = True if 'other_repo_check' in form.data else False
        repo_display = 'none' if other_repo_check else 'block'
        other_repo_display = 'block' if other_repo_check else 'none'
        context = {
            'source_title': 'GitHub',
            'app_type': APP_TYPE,
            'repo_display': repo_display,
            'other_repo_display': other_repo_display,
            'form': form
        }
        return render(self.request, 'plugins/github_import_form.html', context, status=200)
    
    def process_form_data(self, form_data):
        other_repo_check  = form_data['other_repo_check']
        if other_repo_check:
            repo = form_data['other_repo']
        else:
            repo = form_data['repo']

        url = self.get_request_url(repo, path=form_data['path'], ref=form_data['ref'])
        return url

    def get_success(self, request, response):
        file_content = response.json().get('content')
        request.session['import_file_name'] = handle_fetched_file(base64.b64decode(file_content))
        
        if self.current_project:
            return redirect('project_update_import', self.current_project.id)
        else:
            return redirect('project_create_import')
