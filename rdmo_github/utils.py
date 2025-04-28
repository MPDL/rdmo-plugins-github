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

attribute_uri_prefix = "https://rdmo.mpdl.mpg.de/terms"
attribute_sha_uri_key_prefix = "project/metadata/publication/github/sha/"

def get_optionset_elements_with_uri(uri):
    optionset_options = OptionSet.objects.get(uri=uri).elements
    return [(option.uri_path, option.text) for option in optionset_options]


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