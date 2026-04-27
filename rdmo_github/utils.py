import logging

from rdmo.domain.models import Attribute
from rdmo.projects.models.value import Value

logger = logging.getLogger(__name__)

attribute_uri_prefix = "https://rdmo.mpdl.mpg.de/terms"
attribute_sha_uri_key_prefix = "project/metadata/publication/github/sha/"

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

def clear_project_value_with_record_id(project, export_format):
    '''Delete project value with record_id if it exists'''
    
    project_sha_value, record_id_attribute = get_project_value_with_record_id(project, export_format)
    if project_sha_value is not None:
        project_sha_value.delete()

def clear_all_project_values_with_record_ids(project):
    ''' Delete all project values with record_id '''
    
    sha_attributes = Attribute.objects.filter(uri__startswith=f'{attribute_uri_prefix}/domain/{attribute_sha_uri_key_prefix}')
    project.values.filter(attribute__in=sha_attributes).delete()