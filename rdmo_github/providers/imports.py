import base64
import logging
from urllib.parse import quote
from functools import reduce, partial

import requests
import yaml

from django.conf import settings
from django.shortcuts import redirect, render
from django.contrib.sites.shortcuts import get_current_site
from django.utils.translation import gettext, gettext_lazy as _

from rdmo.projects.models.value import Value
from rdmo.projects.models.project import Project
from rdmo.questions.models import Catalog
from rdmo.core.imports import handle_fetched_file
from rdmo.projects.imports import RDMOXMLImport
from rdmo.projects.mixins import ProjectImportMixin
from rdmo.projects.utils import save_import_snapshot_values, save_import_tasks, save_import_values, save_import_views
from rdmo.core.plugins import get_plugin
from rdmo_maus.forms.custom_validators import FilePathExtensionValidator

from ..mixins import GitHubProviderMixin
from ..forms.forms import GitHubImportForm
from ..utils import groupby_values, get_optionset_options, get_questionsets, get_pages

logger = logging.getLogger(__name__)

APP_TYPE = settings.GITHUB_PROVIDER['app_type']

class GitHubImportProvider(GitHubProviderMixin, ProjectImportMixin, RDMOXMLImport):
    @property
    def import_choices(self):
        import_choices =[
            ('False,data/smp.xml', (_('RDMO XML'), _('File path')), 'xml'),
            ('False,CITATION.cff', ('CITATION', _('File path')), 'citation'),
            ('False', 'LICENSE', 'license'),
            ('False', _('Repository dependency graph'), 'sbom'),
            ('False', _('Repository languages'), 'languages')
        ]

        return import_choices
    
    @property
    def import_choice_validators(self):
        import_choice_validators = {}

        valid_extensions = {
            'xml': '.xml',
            'citation': '.cff'
        }

        for choice_key in ['xml', 'citation']:
            import_choice_validators[choice_key] = {
                'text': [FilePathExtensionValidator(valid_extensions.get(choice_key))]
            }
        
        return import_choice_validators
    
    @property
    def import_choice_attributes(self):
        import_choice_attributes = {}
        for c in self.import_choices:
            simple_checkbox = False
            values = c[0].split(',')
            if isinstance(values, list) and len(values) == 1:
                simple_checkbox = True

            choice_key = c[2]
            if not simple_checkbox:
                import_choice_attributes[choice_key] = {
                    'text': {
                        'placeholder': _('example_folder/example_file.extension'),
                    }
                }

        return import_choice_attributes

    def render(self):
        self.pop_from_session(self.request, 'github_import_choice_warnings')
        redirect_url = self.request.build_absolute_uri()
        self.process_app_context(self.request, redirect_url=redirect_url)
        
        access_token = self.validate_access_token(self.request, self.get_from_session(self.request, 'access_token'))
        if access_token is None:
            return self.authorize(self.request)
        
        context = {
            'source_title': 'GitHub',
            'repo_display': 'block',
            'other_repo_display': 'none',
            'form': self.get_form(
                self.request, 
                GitHubImportForm, 
                import_choices=self.import_choices,
                import_choice_validators=self.import_choice_validators,
                import_choice_attributes=self.import_choice_attributes
            )
        }
        return render(self.request, 'plugins/github_import_form.html', context, status=200)

    def submit(self):
        if 'cancel' in self.request.POST:
            if self.current_project is None:
                return redirect('projects')
            else:
                return redirect('project', self.current_project.id)
            
        method = self.request.POST.get('method')
        if method == 'import_repo_subset':
            return getattr(self, method)()

        return self.process_form_submission()
    
    def get_success(self, request, response):
        request_urls = self.pop_from_session(self.request, 'request_urls')
        import_choice_warnings = self.pop_from_session(self.request, 'github_import_choice_warnings')
        
        failed_import_choices = []
        for c in self.import_choices: 
            if isinstance(import_choice_warnings, dict) and c[2] in import_choice_warnings.keys():
                choice_label = c[1][0] if isinstance(c[1], tuple) else c[1]
                failed_import_choices.append(choice_label)

        access_token = self.get_from_session(request, 'access_token')
        headers = self.get_authorization_headers(access_token)

        # 1. Create (or extract from repo xml file) Project() instance
        import_project, xml_import_plugin = self.get_import_project(headers, response, request_urls)
                
        # 2. Create Value() instances for all import data found in repo (and repo xml file if exists)
        import_values = self.get_import_values(import_project, headers, response, xml_import_plugin, request_urls)
        
        if len(import_values) == 0:
            return render(self.request, 'core/error.html', {
                'title': _('Import error'),
                'errors': [_("No values for this project's catalog were found.")]
            }, status=200)

        # 3. Create xml file with all info from repo (and from xml file in repo if exists)
        xml_response = self.create_import_xml_file(request, import_project, import_values, xml_import_plugin, request_urls)
        
        # 4. Pass newly created xml file with all repo imports to ProjectUpdateImportView or ProjectCreateImportView
        request.session['import_file_name'] = handle_fetched_file(xml_response.content)

        if import_choice_warnings is None:
            if self.current_project:
                return redirect('project_update_import', self.current_project.id)
            else:
                return redirect('project_create_import')

        return render(
            self.request, 
            'plugins/github_import_success.html', 
            {'failed_import_choices': failed_import_choices},
            status=200
        )
    
    def import_repo_subset(self):
        if self.current_project:
            return redirect('project_update_import', self.current_project.id)
        else:
            return redirect('project_create_import')
    
    def process_form_submission(self):
        form = self.get_form(
            self.request, 
            GitHubImportForm, 
            self.request.POST, 
            import_choices=self.import_choices,
            import_choice_validators=self.import_choice_validators,
            import_choice_attributes=self.import_choice_attributes
        )

        if form.is_valid():   
            self.request.session['import_source_title'] = self.source_title = 'GitHub'

            # 1. Validate import choices: Check submitted file paths to warn user if repo files don't exist
            import_choice_warnings = self.get_from_session(self.request, 'github_import_choice_warnings')
            if import_choice_warnings is None:
                context, import_choice_warnings = self.validate_import_choices(form.cleaned_data)

                if len(import_choice_warnings) > 0:
                    return render(self.request, 'plugins/github_import_form.html', context, status=200)
            
            # 2. Import selected choices
            urls, import_choice_warnings = self.process_form_data(form.cleaned_data)
            repo_url = urls.pop('repo')

            if len(urls) == 0:
                return render(self.request, 'core/error.html', {
                    'title': _('Import error'),
                    'errors': [_('None of the import choices exists or could be requested.')]
                }, status=200)
            else:
                self.store_in_session(self.request, 'request_urls', urls)
            
            if len(import_choice_warnings) > 0:
                self.store_in_session(self.request, 'github_import_choice_warnings', import_choice_warnings)
            
            return self.make_request(self.request, 'get', repo_url)

        other_repo_check = True if 'other_repo_check' in form.data else False
        repo_display = 'none' if other_repo_check else 'block'
        other_repo_display = 'block' if other_repo_check else 'none'
        context = {
            'source_title': 'GitHub',
            'repo_display': repo_display,
            'other_repo_display': other_repo_display,
            'form': form
        }
        return render(self.request, 'plugins/github_import_form.html', context, status=200)
    
    def check_urls(self, form_data):
        other_repo_check  = form_data['other_repo_check']
        repo = form_data['other_repo'] if other_repo_check else form_data['repo']

        imports = {}
        for i in form_data['imports']:
            i_list = i.split(',')
            key = i_list[0]
            value = quote(i_list[1]) if len(i_list) > 1 else None
            imports[key] = value

        urls = {
            'repo': self.get_request_url(repo),
            'sbom': self.get_request_url(repo, suffix='/dependency-graph/sbom'), # only in default branch
            'languages': self.get_request_url(repo, suffix='/languages'), # only in default branch
            'xml': self.get_request_url(repo, path=imports['xml'], ref=form_data['ref']) if 'xml' in imports else None,
            'citation': self.get_request_url(repo, path=imports['citation'], ref=form_data['ref']) if 'citation' in imports else None,
            'license': self.get_request_url(repo, path=imports['license'], ref=form_data['ref']) if 'license' in imports else None,
        }
        selected_urls = {k:urls.get(k) for k in ['repo', *imports.keys()]}

        access_token = self.get_from_session(self.request, 'access_token')
        import_choice_warnings = {}
        choice_keys = []
        for choice_key, url in selected_urls.items():
            choice_keys.append(choice_key)
            response = requests.get(url, headers=self.get_authorization_headers(access_token))

            try:
                response.raise_for_status()
            except:
                warning = (
                    gettext('There is no file with this path in the selected repository or it cannot be requested') 
                    if imports.get(choice_key) is not None 
                    else gettext('Repository endpoint cannot be requested')
                )
                import_choice_warnings[choice_key] = [warning]

        return import_choice_warnings, choice_keys, selected_urls
    
    def validate_import_choices(self, form_data):
        import_choice_warnings, selected_choice_keys, checked_import_urls = self.check_urls(form_data)
        
        self.store_in_session(self.request, 'github_import_choice_warnings', import_choice_warnings)
        
        selected_choices = [c for c in self.import_choices if c[2] in selected_choice_keys]
        form = self.get_form(
            self.request, 
            GitHubImportForm, 
            self.request.POST, 
            import_choices=selected_choices, 
            import_choice_warnings=import_choice_warnings,
            import_choice_validators=self.import_choice_validators,
            import_choice_attributes=self.import_choice_attributes
        )

        other_repo_check = form_data['other_repo_check']
        repo_display = 'none' if other_repo_check else 'block'
        other_repo_display = 'block' if other_repo_check else 'none'
        context = {
            'source_title': 'GitHub',
            'repo_display': repo_display,
            'other_repo_display': other_repo_display,
            'form': form
        }
        return context, import_choice_warnings
    
    def process_form_data(self, form_data):
        self.pop_from_session(self.request, 'github_import_choice_warnings')

        new_choice_warnings, __, new_urls = self.check_urls(form_data)

        selected_urls = {}
        for choice_key, url in new_urls.items():
            if choice_key not in new_choice_warnings.keys():
                selected_urls[choice_key] = url
        
        return selected_urls, new_choice_warnings
    
    def merge_licenses(self, new_license_values, import_values):
        existing_license_option_uris = [
            v.option.uri for v in import_values 
            if v.attribute.uri == 'https://rdmorganiser.github.io/terms/domain/smp/software-license'
        ]
        
        grouped_new_license_values = reduce(partial(groupby_values, groupby='option'), new_license_values, {}) 
        unique_new_license_values = [value_list[0] for value_list in grouped_new_license_values.values()]
        for v in unique_new_license_values:
            license_option_uri =v.option.uri
            if (
                len(existing_license_option_uris) == 0 or
                license_option_uri not in existing_license_option_uris
            ):
                import_values.append(v)

        return import_values
  
    def get_repo_license(self, url, import_values, headers, response=None, license_id=None):
        # 1. Get license option
        license_options = get_optionset_options('https://rdmorganiser.github.io/terms/options/software-license')

        license_dict = response.json().get('license') if response else {}
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
        v_attribute = self.get_attribute('https://rdmorganiser.github.io/terms/domain/smp/software-license')
        if v_attribute:
            license_text = license_id if license_option.uri == 'https://rdmorganiser.github.io/terms/options/software-license/other-license' else ''
            value = Value()
            value.attribute = v_attribute
            value.set_collection = False
            value.collection_index = collection_index
            value.text = license_text
            value.option = license_option

            import_values = self.merge_licenses([value], import_values)
                
        return import_values
    
    def merge_languages(self, new_language_values, import_values):
        import_values_languages = [
            (v.collection_index, v.text) for v in import_values 
            if v.attribute.uri == 'https://rdmorganiser.github.io/terms/domain/smp/language'
        ]

        project_languages = []
        if self.current_project:
            project_languages = [
                (v.collection_index, v.text)
                for v in self.current_project.values.filter(attribute__uri='https://rdmorganiser.github.io/terms/domain/smp/language') \
                .order_by('collection_index')
            ]

        grouped_new_language_values = reduce(partial(groupby_values, groupby='text'), new_language_values, {}) 
        unique_new_language_values = [value_list[0] for value_list in grouped_new_language_values.values()]
                
        matching_languages = {} # imported languages that already exist in current_project
        for import_index, language_value in enumerate(unique_new_language_values):
            language = language_value.text
            matching_project_language_index, matching_project_language = next(
                ((i, l) for (i, l) in project_languages if l.lower() == language.lower()),
                (None, None)
            )

            if matching_project_language:
                language_value.collection_index = matching_project_language_index
                matching_languages[import_index] = language_value

        new_values = []
        index_to_update = []
        for i, v in enumerate(unique_new_language_values):
            language = v.text.lower()
            if i in matching_languages.keys():
                new_values.append(matching_languages.get(i))
                continue

            index = i + len(import_values_languages)
            if index in [j for (j, l) in project_languages]:
                index_to_update.append(v)
                continue
            
            if (len(import_values_languages) == 0 or language not in [l.lower() for (j, l) in import_values_languages]):
                v.collection_index = index
                new_values.append(v)

        if len(index_to_update) > 0:
            usable_matching_language_indizes = [i for i in matching_languages.keys() if i not in [j for (j, o) in project_languages]]
            new_values_language_indizes = [v.collection_index for v in new_values]
            
            # starting_index accounts for project languages, import_values languages and new_values languages
            max_project_language_index = max([j for (j, l) in project_languages]) if len(project_languages) > 0 else 0
            max_import_values_language_index = max([j for (j, l) in import_values_languages]) if len(import_values_languages) > 0 else 0
            max_new_values_language_index = max(new_values_language_indizes) if len(new_values_language_indizes) > 0 else 0
            starting_index = 1 + max(max_project_language_index, max_import_values_language_index, max_new_values_language_index)
            
            available_indizes = [
                *usable_matching_language_indizes, 
                *[j for j in range(starting_index, (starting_index + len(index_to_update)))]
            ]
            
            for i, v in enumerate(index_to_update):
                index = available_indizes[i]
                v.collection_index = index
                new_values.append(v)

        import_values.extend(new_values)

        return import_values
    
    def get_repo_languages(self, url, import_values, headers):
        response = requests.get(url, headers=headers)
        languages = []
        try:
            response.raise_for_status()
            languages = response.json().keys()
        except:
            pass

        v_attribute = self.get_attribute('https://rdmorganiser.github.io/terms/domain/smp/language')
        if v_attribute:
            new_language_values = []
            for language in languages:      
                value = Value()
                value.attribute = v_attribute
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
        grouped_new_dependencies_values = reduce(partial(groupby_values, groupby='text'), new_dependencies_values, {}) 
        unique_new_dependencies_texts = [value_list[0].text for value_list in grouped_new_dependencies_values.values()]
        new_dependencies_text = '\n'.join(unique_new_dependencies_texts)
        
        # if no value for dependencies in import_values, then append the first new value with all dependency texts (if many)
        if len(existing_dependencies_value_list) == 0 and len(new_dependencies_values) > 0:
            new_value = new_dependencies_values[0]
            new_value.text = new_dependencies_text
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
        grouped_new_dependency_licenses_values = reduce(partial(groupby_values, groupby='text'), new_dependency_licenses_values, {}) 
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
            
        return import_values
    
    def get_repo_dependencies(self, url, import_values, headers):
        response = requests.get(url, headers=headers)
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

            dependencies_str += f'{name}\n'

            license = d.get('licenseConcluded') if 'licenseConcluded' in d else (
                d.get('licenseDeclared') if 'licenseDeclared' in d else None
            )
            if isinstance(license, str):
                license = license.split(' AND')
                license = ','.join(license[:-1]) + _(' and') + license[-1] if len(license) > 1 else license[0] 
            
            if license and license in dependency_licenses:
                dependency_licenses[license].append(name)
            elif license and license not in dependency_licenses:
                dependency_licenses[license] = [name]
        
        dependencies_v_attribute = self.get_attribute('https://rdmorganiser.github.io/terms/domain/smp/external-components')
        if len(dependencies_str) > 0 and dependencies_v_attribute:
            dependencies_value = Value()
            dependencies_value.attribute = dependencies_v_attribute
            dependencies_value.set_collection = False
            dependencies_value.text = dependencies_str

            import_values = self.merge_dependencies([dependencies_value], import_values)

        dependency_licenses_v_attribute = self.get_attribute('https://rdmorganiser.github.io/terms/domain/smp/third-party-licenses')
        if len(dependency_licenses) > 0 and dependency_licenses_v_attribute:
            dependency_licenses_str = ''
            for k, v in dependency_licenses.items():
                dependency_licenses_str += f'{k} ({", ".join(v)})\n'
            
            dependency_licenses_value = Value()
            dependency_licenses_value.attribute = dependency_licenses_v_attribute
            dependency_licenses_value.set_collection = False
            dependency_licenses_value.text = dependency_licenses_str

            import_values = self.merge_dependency_licenses([dependency_licenses_value], import_values)

        application_class_v_attribute = self.get_attribute('https://rdmorganiser.github.io/terms/domain/smp/application-class')
        application_class_option_uri = (
            'https://rdmorganiser.github.io/terms/options/application-class/2'
            if len(dependency_licenses) > 0 
            else 'https://rdmorganiser.github.io/terms/options/application-class/1'
        )
        application_class_option = self.get_option(application_class_option_uri)
        if (
            (len(dependencies_str) > 0 or len(dependency_licenses) > 0) and 
            application_class_v_attribute and
            application_class_option
        ):
            application_class_value = Value()
            application_class_value.attribute = application_class_v_attribute
            application_class_value.set_collection = False
            application_class_value.option = application_class_option
            
            import_values = self.merge_application_class([application_class_value], import_values)

        return import_values

    def merge_title(self, new_title_values, import_values):
        existing_title_value_list = [
            v for v in import_values 
            if v.attribute.uri == 'https://rdmorganiser.github.io/terms/domain/project/title'
        ]

        if len(existing_title_value_list) == 0:
            import_values.append(new_title_values[0])

        return import_values
    
    def merge_authors(self, new_author_values, import_values):
        import_values_author_indizes = [
            v.set_index for v in import_values 
            if v.attribute.uri == 'https://rdmorganiser.github.io/terms/domain/project/partner/id'
        ]
        import_values_orcids = [
            v.text for v in import_values 
            if v.attribute.uri == 'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/orcid'
        ]
        
        project_author_indizes = []
        project_orcids = []
        if self.current_project:
            project_author_indizes = [
                v.set_index 
                for v in self.current_project.values.filter(attribute__uri='https://rdmorganiser.github.io/terms/domain/project/partner/id') \
                .order_by('set_index')
            ]
            project_orcids = [
                (v.set_index, v.text)
                for v in self.current_project.values.filter(attribute__uri='https://rdmo.mpdl.mpg.de/terms/domain/project/partner/orcid') \
                .order_by('set_index')
            ]
        
        employment_attributes = [
            'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/role',
            'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/affiliation',
            'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/affiliation/ror-autocomplete',
            'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/affiliation/ror-id'
        ]

        grouped_new_author_values = reduce(
            partial(groupby_values, groupby='set_index'), 
            [v for v in new_author_values if v.attribute.uri not in employment_attributes], 
            {}
        )
        grouped_new_author_values = reduce(
            partial(groupby_values, groupby='set_prefix'), 
            [v for v in new_author_values if v.attribute.uri in employment_attributes], 
            grouped_new_author_values
        )
        
        matching_authors = {} # imported authors that already exist in current_project
        for import_index, author_values_list in grouped_new_author_values.items():
            author_orcid = next(
                (v.text for v in author_values_list 
                 if v.attribute.uri == 'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/orcid'), 
                None
            )       
            matching_project_author_index, matching_project_orcid = next(
                ((i, o) for (i, o) in project_orcids if author_orcid is not None and o == author_orcid),
                (None, None)
            )

            if matching_project_orcid:
                for v in author_values_list:
                    attr_uri = v.attribute.uri
                    
                    if attr_uri not in employment_attributes:
                        v.set_index = matching_project_author_index

                    if attr_uri in employment_attributes:
                        v.set_prefix = str(matching_project_author_index) # set_prefix is a string field

                matching_authors[int(import_index)] = author_values_list

        new_values = []
        index_to_update = []
        for i, (import_index, author_values_list) in enumerate(grouped_new_author_values.items()):
            author_orcid = next(
                (v.text for v in author_values_list 
                 if v.attribute.uri == 'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/orcid'), 
                None
            )
            if author_orcid in import_values_orcids:
                continue

            if int(import_index) in matching_authors.keys():
                new_values.extend(matching_authors.get(int(import_index)))
                continue

            index = i + len(import_values_author_indizes)
            if index in project_author_indizes:
                index_to_update.append(author_values_list)
                continue

            for v in author_values_list:
                attr_uri = v.attribute.uri

                if attr_uri not in employment_attributes:
                    v.set_index = index

                if attr_uri in employment_attributes:
                    v.set_prefix = str(index) # set_prefix is a string field

                new_values.append(v)

        if len(index_to_update) > 0:
            remaining_matching_author_indizes = [i for i in matching_authors.keys() if i not in project_author_indizes]
            new_values_author_indizes = [
                v.set_index for v in new_values
                if v.attribute.uri == 'https://rdmorganiser.github.io/terms/domain/project/partner/id'
            ]

            # starting_index accounts for project authors, import_values authors and new_values authors
            max_project_author_index = max(project_author_indizes) if len(project_author_indizes) > 0 else 0
            max_import_values_author_index = max(import_values_author_indizes) if len(import_values_author_indizes) > 0 else 0
            max_new_values_author_index = max(new_values_author_indizes) if len(new_values_author_indizes) > 0 else 0
            starting_index = 1 + max(max_project_author_index, max_import_values_author_index, max_new_values_author_index)
            
            available_indizes = [
                *remaining_matching_author_indizes, 
                *[j for j in range(starting_index, (starting_index + len(index_to_update)))]
            ]

            for i, author_values_list in enumerate(index_to_update):
                index = available_indizes[i]
                for v in author_values_list:
                    attr_uri = v.attribute.uri

                    if attr_uri not in employment_attributes:
                        v.set_index = index

                    if attr_uri in employment_attributes:
                        v.set_prefix = str(index) # set_prefix is a string value

                    new_values.append(v)
        

        import_values.extend(new_values)
        merged_new_authors = True if len(new_values) > 0 else False
        
        return import_values, merged_new_authors

    def merge_identifiers(self, new_identifier_values, import_values):
        existing_identifier_option_uris = [
            v.option.uri for v in import_values 
            if v.attribute.uri == 'https://rdmorganiser.github.io/terms/domain/smp/software-pid'
        ]
        
        grouped_new_identifier_values = reduce(partial(groupby_values, groupby='option'), new_identifier_values, {}) 
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

        application_class_v = next(
            v for v in application_class_values 
            if int(v.option.uri.split('/')[-1]) == highest_class_option
        )

        if len(existing_application_class_value_list) == 0:
            import_values.append(application_class_v)
        else:
            application_class_value_index, application_class_value = existing_application_class_value_list[0]
            application_class_value.option = application_class_v.option

            duplicated_application_class_value_indices = [
                i for (i, v) in existing_application_class_value_list 
                if i != application_class_value_index
            ]
            if len(duplicated_application_class_value_indices) > 0:
                for i in duplicated_application_class_value_indices:
                    import_values.pop(i)

        return import_values
        
    def get_cff_title(self, cff_data, import_values):
        cff_value = cff_data.get('title')
        v_attribute = self.get_attribute('https://rdmorganiser.github.io/terms/domain/project/title')

        if cff_value and v_attribute:
            title_value = Value()
            title_value.attribute = v_attribute
            title_value.set_collection = False
            title_value.text = cff_value
            
            import_values = self.merge_title([title_value], import_values)

        return import_values
    
    def get_cff_license(self, cff_data, import_values, url, headers):
        cff_license = cff_data.get('license')

        if cff_license is None:
            return import_values
        elif isinstance(cff_license, list):
            for _id in cff_license:
                import_values = self.get_repo_license(url, import_values, headers, license_id=_id)
        else:
            import_values = self.get_repo_license(url, import_values, headers, license_id=cff_license)

        return import_values
    
    def get_cff_authors(self, cff_data, import_values):
        author_values = []
        attributes = {
            'family-names': self.get_attribute('https://rdmo.mpdl.mpg.de/terms/domain/project/partner/family-name'),
            'given-names': self.get_attribute('https://rdmo.mpdl.mpg.de/terms/domain/project/partner/given-name'),
            'name': self.get_attribute('https://rdmorganiser.github.io/terms/domain/project/partner/name'),
            'orcid': self.get_attribute('https://rdmo.mpdl.mpg.de/terms/domain/project/partner/orcid'),
            'website': self.get_attribute('https://rdmo.mpdl.mpg.de/terms/domain/project/partner/website'),
            'affiliation': self.get_attribute('https://rdmo.mpdl.mpg.de/terms/domain/project/partner/affiliation')
        }
        for i, author in enumerate(cff_data.get('authors', [])):
            type = (
                'person' 
                if (author.get('given-names') or author.get('family-names'))
                else 'entity'
            )

            set_v_attribute = self.get_attribute('https://rdmorganiser.github.io/terms/domain/project/partner/id')
            if set_v_attribute is None:
                continue

            # SET VALUE
            set_label = (
                f'{author.get("given-names", "")} {author.get("family-names", "")}'.replace('None', '').strip()
                if type == 'person'
                else author.get('name', None)
            )
            set_label = set_label if (set_label and set_label != '') else f'cff author # {i+1}'
            set_id_value = Value()
            set_id_value.attribute = set_v_attribute
            set_id_value.set_index = i
            set_id_value.set_collection = True
            set_id_value.text = set_label            
            author_values.append(set_id_value)

            # TYPE VALUE
            type_v_attribute = self.get_attribute('https://rdmo.mpdl.mpg.de/terms/domain/project/partner/type')
            option_uri = (
                    'https://rdmo.mpdl.mpg.de/terms/options/partner-types/person'
                    if type == 'person'
                    else 'https://rdmo.mpdl.mpg.de/terms/options/partner-types/entity'
                )
            type_v_option = self.get_option(option_uri)
            if type_v_attribute and type_v_option:
                set_type_value = Value()
                set_type_value.attribute = type_v_attribute
                set_type_value.set_index = i
                set_type_value.set_collection = True
                set_type_value.option = type_v_option            
                author_values.append(set_type_value)
            
            
            for k, v in author.items():
                if v is None:
                    continue

                # no SMP field for name or orcid for an author of type person, but possible by cff schema
                if (
                    ((k == 'name' or k == 'website') and type == 'person') or
                    (k == 'orcid' and type == 'entity')
                ):
                    continue

                v_attribute = attributes.get(k)
                if k in attributes and k != 'affiliation' and v_attribute:
                    author_value = Value()
                    author_value.attribute = v_attribute
                    author_value.set_index = i
                    author_value.set_collection = True
                    author_value.text = v
                    author_values.append(author_value)
                
                elif k == 'affiliation' and v_attribute:
                    cff_a_str = v
                    affiliations = cff_a_str.split(' & ')
                    for j, a in enumerate(affiliations):
                        affiliation_value = Value()
                        affiliation_value.attribute = v_attribute
                        affiliation_value.set_prefix = str(i) # set_prefix is a string field
                        affiliation_value.set_index = j
                        affiliation_value.set_collection = True
                        affiliation_value.text = a
                        author_values.append(affiliation_value)

        found_new_authors = False
        if len(author_values) > 0:
            import_values, found_new_authors = self.merge_authors(author_values, import_values)

        return import_values, found_new_authors
    
    def get_identifier_option(self, identifier_type):
        options = get_optionset_options('https://rdmorganiser.github.io/terms/options/software_identifier')

        collection_index, option = next(
            ((i, o) for i, o in enumerate(options) if identifier_type and o.uri.endswith(identifier_type)), 
            (None, None)
        )
        
        return collection_index, option
    
    def get_cff_identifiers(self, cff_data, import_values):
        _identifiers = []
        _identifier_types = []
        if 'identifiers' in cff_data:
            _identifiers.extend(cff_data.get('identifiers', []))
            _identifier_types.extend([i.get('type') for i in cff_data.get('identifiers', [])])
        if 'doi' in cff_data and 'doi' not in _identifier_types:
            _identifiers.append({'type': 'doi', 'value': cff_data.get('doi')})
        if 'url' in cff_data and 'url' not in _identifier_types:
            _identifiers.append({'type': 'url', 'value': cff_data.get('url')})

        identifier_values = []
        for identifier in _identifiers:
            value = identifier.get('value')
            if value is None:
                continue

            identifier_type = identifier.get('type')
            collection_index, identifier_option = self.get_identifier_option(identifier_type)
            v_attribute = self.get_attribute('https://rdmorganiser.github.io/terms/domain/smp/software-pid')
            if identifier_option and v_attribute:
                identifier_value = Value()
                identifier_value.attribute = v_attribute
                identifier_value.set_collection = False
                identifier_value.collection_index = collection_index
                identifier_value.text = value
                identifier_value.option = identifier_option
                identifier_values.append(identifier_value)

        found_new_identifiers = False
        if len(identifier_values) > 0:
            import_values, found_new_identifiers = self.merge_identifiers(identifier_values, import_values)

        return import_values, found_new_identifiers
    
    def get_repo_citation_file(self, url, import_values, headers):
        # https://github.com/citation-file-format/citation-file-format/blob/main/schema-guide.md
        cff_data = {}
        response = requests.get(url, headers=headers)
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
            import_values = self.get_cff_license(cff_data, import_values, url, headers)

        found_new_authors = False
        if 'authors' in cff_data:
            import_values, found_new_authors = self.get_cff_authors(cff_data, import_values)

        found_new_identifiers = False
        if 'identifiers' in cff_data or 'doi' in cff_data or 'url' in cff_data:
            import_values, found_new_identifiers = self.get_cff_identifiers(cff_data, import_values)
        
        application_class_v_attribute = self.get_attribute('https://rdmorganiser.github.io/terms/domain/smp/application-class')
        application_class_option_uri = (
                'https://rdmorganiser.github.io/terms/options/application-class/2'
                if found_new_authors
                else 'https://rdmorganiser.github.io/terms/options/application-class/1'
            )
        application_class_option = self.get_option(application_class_option_uri)
        if (
            (found_new_authors or found_new_identifiers) and 
            application_class_v_attribute and 
            application_class_option
        ):
            application_class_value = Value()
            application_class_value.attribute = application_class_v_attribute
            application_class_value.set_collection = False
            application_class_value.option = application_class_option
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
           
    def get_xml_values(self, url, import_values, headers, xml_import_plugin):
        if xml_import_plugin is None:
            return import_values
        
        # if xml values have old attributes that do not exist in catalog anymore v.attribute == None
        new_values = [v for v in xml_import_plugin.values if v.attribute]
        xml_import_plugin.values = new_values
        
        author_attribute_uris = [
            'https://rdmorganiser.github.io/terms/domain/project/partner/id',
            'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/type',
            'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/family-name',
            'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/given-name',
            'https://rdmorganiser.github.io/terms/domain/project/partner/name',
            'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/orcid',
            'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/orcid-autocomplete',
            'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/website',
            'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/role',
            'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/affiliation',
            'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/affiliation/ror-autocomplete',
            'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/affiliation/ror-id'
        ]
        grouped_import_values = reduce(
            partial(groupby_values, groupby='attribute'), 
            [v for v in import_values if v.attribute.uri not in author_attribute_uris], 
            {}
        )
        grouped_import_values['authors'] = [v for v in import_values if v.attribute.uri in author_attribute_uris]
        
        grouped_xml_values = reduce(
            partial(groupby_values, groupby='attribute'), 
            [v for v in xml_import_plugin.values if v.attribute.uri not in author_attribute_uris], 
            {}
        )
        grouped_xml_values['authors'] = [v for v in xml_import_plugin.values if v.attribute.uri in author_attribute_uris]

        for uri, xml_values_list in grouped_xml_values.items():
            if (
                uri not in grouped_import_values.keys() and
                # imported xml authors and languages must always be merged: 
                # they may have different order than matching project values
                uri != 'authors' and 
                uri != 'https://rdmorganiser.github.io/terms/domain/smp/language'
            ):
                import_values.extend(xml_values_list)
            else:
                import_values = self.merge_xml_values(uri, xml_values_list, import_values)

        return import_values
    
    def get_import_values(self, import_project, headers, repo_response, xml_import_plugin, request_urls):
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
            import_values = f(url, import_values, headers, **f_kwargs)
            
        # Keep only values with a corresponding question, question set or page (attribute) 
        # in import_project.catalog (which equals current_project.catalog)
        catalog_questions = self.get_questions(import_project.catalog)
        catalog_questionsets = get_questionsets(import_project.catalog)
        catalog_pages = get_pages(import_project.catalog)
        import_values = [
            v for v in import_values if (
                catalog_questions.get(v.attribute.uri) or
                catalog_questionsets.get(v.attribute.uri) or
                catalog_pages.get(v.attribute.uri)
            )
        ]

        def sort_by_external_id(e):
            # values without an external id come first
            # external id marks values used by option providers
            return e.external_id

        import_values.sort(key = sort_by_external_id)

        return import_values

    def get_import_project(self, headers, repo_response, request_urls):
        '''Return a Project() instance that will be the basis to create an xml with all the info
        from the selected repository. 
        
        If the user fills out the path to an RDMO xml file (optional), the Project() instance
        will have its information. If no file path is filled out, the Project() instance will be created from scratch.
        '''

        catalog = (
            self.current_project.catalog if self.current_project 
            else Catalog.objects.get(uri='https://rdmorganiser.github.io/terms/questions/smp')
        )
        title = (
            repo_response.json().get('html_url').split('/')[-1] 
            if repo_response.json().get("html_url")
            else _('GitHub Import')
        )
        import_project = Project(
            catalog=catalog,
            title=title
        )
        xml_import_plugin = None
        
        if 'xml' in request_urls:
            xml_response = requests.get(request_urls['xml'], headers=headers)

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
                            if xml_import_plugin.project
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
        # 1. If Value() for title (title_value) exists and title_value != import_project.title, 
        # update import_project.title if first import source was xml
        title = next(
            (v.text for v in import_values 
             if v.attribute.uri == 'https://rdmorganiser.github.io/terms/domain/project/title'), 
            None
        )
        first_import_source = list(request_urls.keys())[0]
        if title and title != import_project.title and first_import_source != 'xml':
            import_project.title = title

        checked = [
            f'{v.attribute.uri}[{v.set_prefix}][{v.set_index}][{v.collection_index}]'
            for v in import_values
        ]

        snapshots = xml_import_plugin.snapshots if xml_import_plugin else []
        self.update_values(None, import_project.catalog, import_values, snapshots)

        import_project.site = get_current_site(request)
        import_project.save()

        tasks = xml_import_plugin.tasks if xml_import_plugin else []
        views = xml_import_plugin.views if xml_import_plugin else []
        save_import_values(import_project, import_values, checked)
        save_import_snapshot_values(import_project, snapshots, checked)
        save_import_tasks(import_project, tasks)
        save_import_views(import_project, views)
        
        xml_import_plugin = get_plugin('PROJECT_EXPORTS', 'xml')
        xml_import_plugin.project = import_project
        xml_response = xml_import_plugin.render()
        
        Project.objects.filter(pk=import_project.id).delete()

        return xml_response


class GitHubImport(GitHubProviderMixin, RDMOXMLImport):

    def render(self):
        redirect_url = self.request.build_absolute_uri()
        self.process_app_context(self.request, redirect_url=redirect_url)
        
        access_token = self.validate_access_token(self.request, self.get_from_session(self.request, 'access_token'))
        if access_token is None:
            return self.authorize(self.request)
        
        context = {
            'source_title': 'GitHub',
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
