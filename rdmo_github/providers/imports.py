import base64
import logging
from functools import reduce
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

        urls = {
            'repo': self.get_request_url(repo),
            'sbom': self.get_request_url(repo, suffix='/dependency-graph/sbom'),
            'languages': self.get_request_url(repo, suffix='/languages'),
            'contents': self.get_request_url(repo, suffix='/contents')
        }

        rdmo_xml_file_path = self.get_request_url(repo, path=form_data['path'], ref=form_data['ref']) if 'path' in form_data else None
        if rdmo_xml_file_path is not None:
            urls.update({'rdmo_xml_file_path': rdmo_xml_file_path})

        return urls
    
    def merge_unique_values(self, initial, values):
        xml_values, repo_values = values

        if len(xml_values) == 0:
            initial.extend(repo_values)
            return initial

        xml_keys = [
            f'{v.attribute.uri}[{v.set_prefix}][{v.set_index}][{v.collection_index}]'
            for v in xml_values
        ]
        for v in repo_values:
            k = f'{v.attribute.uri}[{v.set_prefix}][{v.set_index}][{v.collection_index}]'
            if k not in xml_keys:
                initial.append(v)
        
        return initial
    
    def get_repo_license(self, repo_values, response=None, license_id=None):
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
            return repo_values
        
        # 2. Create license value with correct option
        # Only append unique new license values
        license_values = [v for v in repo_values if v.attribute.uri == 'https://rdmorganiser.github.io/terms/domain/smp/software-license']
        existing_license_option_uris = [v.option.uri for v in license_values]
        if (
            len(existing_license_option_uris) == 0 or
            license_option.uri not in existing_license_option_uris
        ):
            license_text = license_id if license_option.uri == 'https://rdmorganiser.github.io/terms/options/software-license/other-license' else ''
            value = Value()
            value.attribute = Attribute.objects.get(uri='https://rdmorganiser.github.io/terms/domain/smp/software-license')
            value.set_collection = False
            value.collection_index = collection_index
            value.text = license_text
            value.option = license_option
            repo_values.append(value)
        
        return repo_values
    
    def get_identifier_option(self, identifier_type):
        options = OptionSet.objects.get(uri='https://rdmorganiser.github.io/terms/options/software_identifier').elements

        collection_index, option = next(
            ((i, o) for i, o in enumerate(options) if o.uri.endswith(identifier_type)), 
            (None, None)
        )
        
        return collection_index, option

    def get_repo_languages(self, url, repo_values):
        response = requests.get(url)
        languages = []
        try:
            response.raise_for_status()
            languages = response.json().keys()
        except:
            pass
        
        for i, language in enumerate(languages):
            value = Value()
            # project=project,
            value.attribute = Attribute.objects.get(uri='https://rdmorganiser.github.io/terms/domain/smp/language')
            value.set_collection = False
            value.collection_index = i
            value.text = language
            repo_values.append(value)

        return repo_values
    
    def get_repo_dependencies(self, url, repo_values):
        response = requests.get(url)

        try:
            response.raise_for_status()
            sbom = response.json().get('sbom')
        except:
            return repo_values
        
        dependencies_str = ''
        dependency_licenses = {}
        for d in sbom.get('packages', []):
            name = d.get('name')
            version = d.get('versionInfo')
            license = d.get('licenseConcluded') if 'licenseConcluded' in d else (
                d.get('licenseDeclared') if 'licenseDeclared' in d else None
            )

            dependencies_str += f'{name} {version}\n'
            
            if license is not None and license in dependency_licenses:
                dependency_licenses[license].append(name)

            elif license is not None and license not in dependency_licenses:
                dependency_licenses[license] = [name]
            
        if len(dependencies_str) > 0:
            dependencies_value = Value()
            # project=project,
            dependencies_value.attribute = Attribute.objects.get(uri='https://rdmorganiser.github.io/terms/domain/smp/external-components')
            dependencies_value.set_collection = False
            dependencies_value.text = dependencies_str
            repo_values.append(dependencies_value)

        if len(dependency_licenses) > 0:
            dependency_licenses_str = ''
            for k, v in dependency_licenses.items():
                dependency_licenses_str += f'{k} ({", ".join(v)})\n'

            dependency_licenses_value = Value()
            # project=project,
            dependency_licenses_value.attribute = Attribute.objects.get(uri='https://rdmorganiser.github.io/terms/domain/smp/third-party-licenses')
            dependency_licenses_value.set_collection = False
            dependency_licenses_value.text = dependency_licenses_str
            repo_values.append(dependency_licenses_value)

        return repo_values

    def get_repo_contents(self, url):
        response = requests.get(url)

        try:
            response.raise_for_status()
            contents = response.json()
            return {c.get('name').lower(): c.get('url') for c in contents}
        except:
            return None

    def get_repo_citation_file(self, contents_url, repo_values, xml_values):
        # https://github.com/citation-file-format/citation-file-format/blob/main/schema-guide.md
        repo_contents = self.get_repo_contents(contents_url)

        if repo_contents is not None and 'citation.cff' in repo_contents:
            citation_url = repo_contents.get('citation.cff')

            cff_data = {}
            response = requests.get(citation_url)
            try:
                response.raise_for_status()
                encoded_content = response.json().get('content')
                decoded_bytes = base64.b64decode(encoded_content)
                content = decoded_bytes.decode('utf-8')
                cff_data = yaml.safe_load(content)
            except:
                pass
            
            if 'title' in cff_data:
                title_value = Value()
                title_value.attribute = Attribute.objects.get(uri='https://rdmorganiser.github.io/terms/domain/project/title')
                title_value.set_collection = False
                title_value.text = cff_data.get('title')
                repo_values.append(title_value)

            if 'license' in cff_data:
                for _id in cff_data.get('license'):
                    repo_values = self.get_repo_license(repo_values, license_id=_id)
                    
            if 'authors' in cff_data:
                xml_set_id_values = (
                    [v for v in xml_values if v.attribute.uri == 'https://rdmorganiser.github.io/terms/domain/project/partner/id']
                    if isinstance(xml_values, list)
                    else []
                )
                xml_authors_orcids = (
                    [v.text for v in xml_values if v.attribute.uri == 'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/orcid-id']
                    if isinstance(xml_values, list)
                    else []
                )
                
                cff_authors = [a for a in cff_data.get('authors', []) if (a.get('orcid') is None or a.get('orcid') not in xml_authors_orcids)]
                # print(f'cff_authors: {cff_authors}')
                author_values = []
                attributes = {
                    'family-names': 'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/family-name',
                    'given-names': 'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/given-name',
                    'orcid': 'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/orcid-id',
                    'affiliation': 'https://rdmo.mpdl.mpg.de/terms/domain/project/partner/affiliation'
                }
                for i, author in enumerate(cff_authors):
                    set_label = f'{author.get("given-names", "")} {author.get("family-names", "")}'
                    set_id_value = Value()
                    set_id_value.attribute = Attribute.objects.get(uri='https://rdmorganiser.github.io/terms/domain/project/partner/id')
                    set_id_value.set_index = i + len(xml_set_id_values)
                    set_id_value.set_collection = True
                    set_id_value.text = set_label
                    
                    author_values.append(set_id_value)
                    
                    for k, v in author.items():
                        if k in attributes and k != 'affiliation':
                            author_value = Value()
                            author_value.attribute = Attribute.objects.get(uri=attributes.get(k))
                            author_value.set_index = i + len(xml_set_id_values)
                            author_value.set_collection = True
                            author_value.text = v
                            author_values.append(author_value)
                        
                        elif k == 'affiliation':
                            cff_a_str = v
                            affiliations = cff_a_str.split(' & ')
                            for j, a in enumerate(affiliations):
                                affiliation_value = Value()
                                affiliation_value.attribute = Attribute.objects.get(uri=attributes[k])
                                affiliation_value.set_prefix = str(i + len(xml_set_id_values)) # set_prefix is a string field
                                affiliation_value.set_index = j
                                affiliation_value.set_collection = True
                                affiliation_value.text = a
                                author_values.append(affiliation_value)

                if len(author_values) > 0:
                    application_class_value = Value()
                    application_class_value.attribute = Attribute.objects.get(uri='https://rdmorganiser.github.io/terms/domain/smp/application-class')
                    application_class_value.set_collection = False
                    application_class_value.option = Option.objects.get(uri='https://rdmorganiser.github.io/terms/options/application-class/2')
                    repo_values.append(application_class_value)  
                    repo_values.extend(author_values)

            _identifiers = []
            _identifier_types = []
            if 'identifiers' in cff_data:
                _identifiers.extend(cff_data.get('identifiers'))
                _identifier_types.extend([i.get('type') for i in cff_data.get('identifiers', [])])
            if 'doi' in cff_data and 'doi' not in _identifier_types:
                _identifiers.append({'type': 'doi', 'value': cff_data.get('doi')})
            if 'url' in cff_data and 'url' not in _identifier_types:
                _identifiers.append({'type': 'url', 'value': cff_data.get('url')})

            for identifier in _identifiers:
                identifier_type = identifier.get('type')
                collection_index, identifier_option = self.get_identifier_option(identifier_type)
                identifier_value = Value()
                identifier_value.attribute = Attribute.objects.get(uri='https://rdmorganiser.github.io/terms/domain/smp/software-pid')
                identifier_value.set_collection = False
                identifier_value.collection_index = collection_index
                identifier_value.text = identifier.get('value')
                identifier_value.option = identifier_option
                repo_values.append(identifier_value)
        
        else:
            pass
        
        return repo_values

    def get_import_values(self, xml_import_plugin, repo_response, other_request_urls):
        '''Return a list with Value() instances to create an xml with all the info
        from the selected repository.
        
        If the user fills out the path to an RDMO xml file (optional), its Value() instances
        will have precedence over information in the repository.
        '''
        # 1. Check if xml values exist
        xml_values = xml_import_plugin.values if xml_import_plugin is not None else None
        
        # 2. Get repo values
        repo_values = []
        repo_values = self.get_repo_license(repo_values, response=repo_response)
        repo_values = self.get_repo_languages(other_request_urls['languages'], repo_values)
        repo_values = self.get_repo_dependencies(other_request_urls['sbom'], repo_values)
        
        repo_values = self.get_repo_citation_file(other_request_urls['contents'], repo_values, xml_values)
        print(f'len(repo_values): {len(repo_values)}')
        
        # 3. Merge xml and repo values and return unique values (if xml value exists, it has precedence)
        if xml_import_plugin is not None and isinstance(xml_import_plugin.values, list):
            def _groupby_uri(initial, v):
                uri = v.attribute.uri
                if uri not in initial.keys():
                    initial[uri] = [v]
                else:
                    initial[uri].append(v)
                return initial
            repo_values_by_attr_uri = reduce(_groupby_uri, repo_values, {})
            xml_values_by_attr_uri = reduce(_groupby_uri, xml_import_plugin.values, {})
            
            values_by_attr_uri = []
            for uri, repo_v in repo_values_by_attr_uri.items():
                xml_v = xml_values_by_attr_uri[uri] if uri in xml_values_by_attr_uri.keys() else []
                values_by_attr_uri.append((xml_v, repo_v))
            
            new_values_from_repo = reduce(self.merge_unique_values, values_by_attr_uri, [])

            print(f'len(xml_values): {len(xml_values)}')
            print(f'len(new_values_from_repo): {len(new_values_from_repo)}')
            xml_import_plugin.values.extend(new_values_from_repo)
            import_values = xml_import_plugin.values
        
        else:
            import_values = repo_values

        print(f'final: len(import_values): {len(import_values)}')
        return import_values

    def get_import_project(self, repo_response, request_urls):
        '''Return a Project() instance that will be the basis to create an xml with all the info
        from the selected repository. 
        
        If the user fills out the path to an RDMO xml file (optional), the Project() instance
        will have its information. If no file path is filled out, the Project() instance will be created from scratch.
        '''
        catalog = self.current_project.catalog if self.current_project else Catalog.objects.get(uri='https://rdmorganiser.github.io/terms/questions/smp')
        title = (
            _('GitHub Import ({html_url})').format(html_url=repo_response.json().get('html_url')) 
            if repo_response.json().get("html_url") is not None 
            else _('GitHub Import')
        )
        import_project = Project(
            catalog=catalog,
            title=title
        )
        xml_import_plugin = None
        
        if 'rdmo_xml_file_path' in request_urls:
            xml_response = requests.get(request_urls['rdmo_xml_file_path'])

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
    
    def create_import_xml_file(self, request, import_project, import_values, xml_import_plugin):
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
        
        print(f'GitHubImportProvider.values: {self.values}')
        print(f'import_project.values: {import_project.values}')

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
        import_values = self.get_import_values(xml_import_plugin, response, request_urls)
        
        # 3. Create xml file with all info from repo (and from xml file in repo if exists)
        xml_response = self.create_import_xml_file(request, import_project, import_values, xml_import_plugin)

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
