import logging

from rdmo.domain.models import Attribute
from rdmo.projects.models.value import Value
from rdmo.options.models import OptionSet
from rdmo.questions.models import Page, QuestionSet

logger = logging.getLogger(__name__)

attribute_uri_prefix = "https://rdmo.mpdl.mpg.de/terms"
attribute_sha_uri_key_prefix = "project/metadata/publication/github/sha/"

def groupby_values(initial, v, groupby):
    groupby_mapping = {
        'attribute': v.attribute.uri,
        'option': v.option.uri if v.option else None,
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

def get_optionset_options(optionset_uri):
        try:
            options = OptionSet.objects.get(uri=optionset_uri).elements
            return options
        except KeyError:
            logger.info('Optionset %s not in db. Skipping.', optionset_uri)
            return []
        
def get_questionsets(catalog):
    queryset = QuestionSet.objects.filter_by_catalog(catalog) \
                            .select_related('attribute') \
                            .order_by('attribute__uri')

    questionsets = {}
    for questionset in queryset:
        if questionset.attribute and questionset.attribute.uri not in questionsets:
            questionsets[questionset.attribute.uri] = questionset
    return questionsets

def get_pages(catalog):
    queryset = Page.objects.filter_by_catalog(catalog) \
                            .select_related('attribute') \
                            .order_by('attribute__uri')

    pages = {}
    for page in queryset:
        if page.attribute and page.attribute.uri not in pages:
            pages[page.attribute.uri] = page
    return pages

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