from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .custom_widgets import MultivalueCheckboxWidget, MultivalueCheckboxMultipleChoiceWidget

class MultivalueCheckboxField(forms.MultiValueField):
    widget = MultivalueCheckboxWidget

    def __init__(self, simple_checkbox=False):
        fields = (
            (forms.BooleanField(),) if simple_checkbox else
            (    
                forms.BooleanField(),
                forms.CharField(),
            )
        )

        super().__init__(fields)

    def clean(self, value):
        """This method applies to a multi-value field corresponding to 
        a choice in MultivalueCheckboxMultipleChoiceField.
        Every export choice consists of a boolean field and a char field.

        Validate every subvalue in value ([boolean_value, char_value]). 
        Each subvalue is validated against the corresponding Field in self.fields.

        Important: ValidationErrors are NOT raised here, clean() returns
        the choice's errors to the main field MultivalueCheckboxMultipleChoiceField.
        After validating all choices, MultivalueCheckboxMultipleChoiceField 
        raises all ValidationErrors.
        """

        clean_data = []
        errors = []
        if not isinstance(value, list):
            value = self.widget.decompress(value)
        
        for i, field in enumerate(self.fields):
            # i = 0 -> boolean (checkbox for choice selection)
            # i = 1 -> character (text for file path)
            field_value = value[i]

            if field_value in self.empty_values: # self.empty_values = (None, '', [], (), {})
                errors.append(ValidationError(_('A file path is required.'), code='required'))
                
            try:
                clean_data.append(field.clean(field_value))
            except ValidationError as ee:
                # Collect all validation errors of the subfield in a single list 
                # (ee.error_list: list[ValidationError]). Skip duplicates.
                errors.extend(e for e in ee.error_list if e not in errors)

        out = self.compress(clean_data)
        self.validate(out)
        self.run_validators(out)
        return out, errors

    def compress(self, data_list):
        '''Transform input data_list to a string with the correctly typed value for each subwidget:
            - a boolean value for the checkbox
            - a string value for the text
        '''

        if isinstance(data_list, list):
            return ','.join(map(str, data_list))
        
        return 'False'
    

class MultivalueCheckboxMultipleChoiceField(forms.MultipleChoiceField):
    '''
    A multiple choice field with multivalue checkboxes as choices.
    
    CHOICES
    
    Each choice consists of one or two fields:
    - a checkbox field with a label 
    - (optionally) a text input field with a label

    choices = [(values, labels, key), ...] with:

    1. values (str): comma-separated values for each choice. 
    First value is required and is the starting value for the checkbox (as a string: 'True' or 'False'), 
    second value is optional and is the starting value for the text.
    Examples of valid values:
    - 'False,my-text' -> checkbox's starting value is False, text's starting value is 'my-text'
    - 'True,' -> checkbox's starting value is True, text's starting value is ''
    - 'True' -> checkbox's starting value is True, text does NOT exist (no comma)
    
    2. labels (str | tuple[str]): a string or a one or two-value tuple with the checkbox and text field labels (str).
    First value is required, second is optional.
    Examples of valid labels:
    - ('Checkbox label', 'Text label')
    - ('one value tuple with checkbox label',)
    - 'string label for checkbox'

    3. key (str): choice key.
    Examples:
    - 'choice_1'
    - 'pdf-export'

    CHOICE VALIDATORS and CHOICE ATTRIBUTES

    Common validators and attributes can be passed as usual (MultivalueCheckboxMultipleChoiceField(..., validators=validators),
    MultivalueCheckboxMultipleChoiceWidget(..., attrs=attrs)).
    
    Choice validators or choice attributes can also be passed as follows:

    choice_validators (dict[choice_key, dict['checkbox'|'text', list[Validators]]]) is a dictionary with choice_keys 
    as keys and for values dictionaries with validators for the checkbox field and/or the text field. choice_validators 
    only need to contain choice_keys of choices that need validators, and only the field ('checkbox' or 'text') that
    needs validators must be included in the inner dictionary. 
    
    choice_attributes (dict[choice_key, dict['checkbox'|'text', attrs_dict]]) is a dictionary with choice_keys 
    as keys and for values dictionaries with attributes for the checkbox widget and/or the text widget. choice_attributes 
    only need to contain choice_keys of choices with extra attributes, and only the widget ('checkbox' or 'text') that
    needs extra attributes must be included in the inner dictionary.
    
    Examples:

    If there are these choices = ((values_1, labels_1, 'choice_1'), (values_2, labels_2, 'choice_2'), (values_3, labels_3, 'choice_3')),
    these would be valid choice_validators and choice_attributes:
    - choice_validators = {
        'choice_1': {
            'checkbox': [validator_1, validator_2],
            'text': [validator_3]
        },
        'choice_3: {
            'text': [validator_3, validator_4, validator_5]
        }
    }
    - choice_attributes = {
        'choice_1': {
            'checkbox': {'onchange': changeHandler()}
        },
        'choice_2': {
            'checkbox': {'oninput': inputHandler()},
            'text': {'style': 'background-color: blue;'}
        }
    }
    '''

    widget = MultivalueCheckboxMultipleChoiceWidget
    _choice_fields = {}
    _choice_keys = []
    _choice_validators = {}
    _choices_to_update = {}

    @property
    def choices(self):
        return self._choices
    
    @choices.setter
    def choices(self, new_choices):
        self._choices = self.widget.choices = new_choices
        self.choice_fields = new_choices
        self.choice_keys = new_choices
    
    @property
    def choice_fields(self):
        return self._choice_fields

    @choice_fields.setter
    def choice_fields(self, new_choices):
        new_choice_fields = {}
        for c in new_choices:
            simple_checkbox = False
            
            values = c[0].split(',')
            choice_key = c[2]
            if isinstance(values, list) and len(values) == 1:
                simple_checkbox = True

            new_choice_fields[choice_key] = MultivalueCheckboxField(simple_checkbox=simple_checkbox)
        
        self._choice_fields = new_choice_fields

    @property
    def choice_keys(self):
        return self._choice_keys
    
    @choice_keys.setter
    def choice_keys(self, new_choices):
        new_choice_keys = [c[2] for c in new_choices]
        self._choice_keys = self.widget.choice_keys = new_choice_keys

    @property
    def choice_validators(self):
        return self._choice_validators
    
    @choice_validators.setter
    def choice_validators(self, new_choices_validators):
        self._choice_validators = new_choices_validators

    @property
    def choices_to_update(self):
        return self._choices_to_update

    @choices_to_update.setter
    def choices_to_update(self, new_values):
        self._choices_to_update = self.widget.choices_to_update = new_values

    def to_python(self, value):
        if not value:
            return []
        elif not isinstance(value, (list, tuple)):
            raise ValidationError(
                self.error_messages["invalid_list"], code="invalid_list"
            )

        value = [str(multival) for multival in value]

        # value is a list of choices, each choice is a multivalue string with 1 or two comma-separated values:
        # i = 0 -> choice key (checkbox value, fix)
        # i = 1 -> optional file path (input field, modify-able by the user)
        return [','.join(map(lambda x: x.strip(), multival.split(','))) for multival in value]

    def clean(self, value):
        '''Validate the given value and return its 'cleaned' value as an
        appropriate Python object.
        
        Validation of this field implies also validation of each of its choices;
        i.e. clean() raises a ValidationError also if any of the choices is not valid.
        In the case of single invalid choices, ValidationError message for this field 
        is an empty string because the error message is displayed below the 
        corresponding choice(s). 
        '''
        value = self.to_python(value)

        if len(self.validators) > 0:
            self.run_validators()

            return value
        
        if len(self.choice_validators) > 0:
            for choice_key in self.choice_keys:
                checkbox_validators = self.choice_validators.get(choice_key, {}).get('checkbox')
                text_validators = self.choice_validators.get(choice_key, {}).get('text')
                choice_field = self.choice_fields.get(choice_key)

                if checkbox_validators:
                    choice_field.fields[0].validators = checkbox_validators
                
                if text_validators and len(choice_field.fields) > 1:
                    choice_field.fields[1].validators = text_validators
                

        self.widget.errors = {}
        if value in self.empty_values and self.required: # self.empty_values = (None, '', [], (), {})
            raise ValidationError(_('At least one choice must be selected.'), code='required')

        # validate choice values, which consist of multivalues (boolean and char) 
        for multivalue in value:
            multivalue_list = multivalue.split(',')
            choice_key = multivalue_list[0]
            if len(multivalue_list) > 1:
                text_value = multivalue_list[1]
            
            if choice_key in self.choice_keys:
                choice_field = self.choice_fields.get(choice_key)
                choice_value = [True, text_value] if len(multivalue_list) > 1 else [True]
                out, errors = choice_field.clean(choice_value)
                if len(errors) > 0:
                    self.widget.errors[choice_key] = errors
                else:
                    self.widget.errors.pop(choice_key, None)
        
        # raise ValidationError with empty string after passing errors to corresponding choices
        if len(self.widget.errors) > 0:
            raise ValidationError('')
        
        return value