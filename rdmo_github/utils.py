import requests
import logging

from django.http import HttpResponse
from django.template import TemplateSyntaxError

from rdmo.core.utils import render_to_format
from rdmo.domain.models import Attribute
from rdmo.options.models import OptionSet
from rdmo.projects.models.value import Value
from rdmo.projects.utils import get_value_path
from rdmo.views.models import View

logger = logging.getLogger(__name__)

attribute_uri_prefix = "https://dev-rdmo.int.mpdl.mpg.de/terms"
attribute_sha_uri_key_prefix = "project/metadata/publication/github/sha/"

def get_project_licenses(project):
    # https://github.com/spdx/license-list-data
    attribute = Attribute.objects.get(uri='https://rdmorganiser.github.io/terms/domain/smp/software-license')
    spdx_ids = [license.value for license in project.values.filter(attribute=attribute)]
    
    license_contents = []
    for id in spdx_ids:
        url = 'https://api.github.com/repos/spdx/license-list-data/contents/text/{spdx_id}.txt'.format(spdx_id=id)        
        response = requests.get(url, headers={'Accept': 'application/vnd.github+json'})
        try:
            response.raise_for_status()
            base64_string_of_content = response.json().get('content')
            license_contents.append({
                'content': base64_string_of_content,
                'path': f'contents/LICENSE-{id}',
                'export_format': f'license-{id.lower()}'
            })
        except:
            continue
        
    return license_contents


def get_optionset_elements_with_uri(uri):
    optionset_options = OptionSet.objects.get(uri=uri).elements
    return [(option.uri_path, option.text) for option in optionset_options]


def render_project_views(project, snapshot, attachments_format, view_uri):
    view = View.objects.get(uri=view_uri)

    try:
        rendered_view = view.render(project, snapshot)
    except TemplateSyntaxError:
        return HttpResponse()

    return render_to_format(
        None, attachments_format, project.title, 'projects/project_view_export.html', {
            'format': attachments_format,
            'title': project.title,
            'view': view,
            'rendered_view': rendered_view,
            'resource_path': get_value_path(project, snapshot)
        }
    )

def get_project_value_with_record_id(project, export_format):
    record_id_attribute, _created = Attribute.objects.get_or_create(uri_prefix=attribute_uri_prefix,
                                                          key=f'{attribute_sha_uri_key_prefix}{export_format}')
    
    project_sha_value = project.values.filter(attribute=record_id_attribute).first()
    return project_sha_value, record_id_attribute

def get_record_id_from_project_value(project, export_format):
    project_sha_value, _record_id_attribute = get_project_value_with_record_id(project, export_format)

    if project_sha_value is not None:
        return project_sha_value.text
    else:
        return None

def set_record_id_on_project_value(project, record_id, export_format):
    if project is None or record_id is None:
        return

    project_sha_value, record_id_attribute = get_project_value_with_record_id(project, export_format)

    if project_sha_value is None:
        # create the value with text and add it
        value = Value(project=project, attribute=record_id_attribute, text=record_id)
        value.save()
        project.values.add(value)
    elif project_sha_value.text != record_id:
        # update and overwrite the value.text
        project_sha_value.text = record_id
        project_sha_value.save()

def clear_record_id_from_project_value(project, export_format):
    """Clear the record_id text from the project's values by setting it to an empty string."""
    set_record_id_on_project_value(project, '', export_format)