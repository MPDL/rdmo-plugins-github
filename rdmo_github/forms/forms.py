from django import forms
from django.templatetags.static import static
from django.utils.html import format_html
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from rdmo_maus.forms.fields import MultivalueCheckboxMultipleChoiceField

from .validators import validate_new_repo_name, validate_import_file_path

class GithubBaseForm(forms.Form):
    def __init__(self, *args, **kwargs):
        repo_choices = kwargs.pop('repo_choices')
        repo_help_text = kwargs.pop('repo_help_text')
        super().__init__(*args, **kwargs)

        if repo_choices is not None:
            self.fields['repo'].choices = repo_choices
            
        if repo_help_text is not None:
            self.fields['repo'].help_text = repo_help_text

class GitHubExportForm(GithubBaseForm):
    def __init__(self, *args, **kwargs):
        repo_choices = kwargs.get('repo_choices')
        export_choices = kwargs.pop('export_choices', None)
        super().__init__(*args, **kwargs)

        if repo_choices is not None and len(repo_choices) == 0:
            self.fields['new_repo'].initial = True

        if export_choices is not None:
            self.fields['exports'].choices = export_choices.get('choices')
            self.fields['branch'].widget = forms.TextInput(
                attrs={'oninput': f"hideAllChoiceWarningMessages(this, {len(export_choices.get('choices'))})"}
            )
            self.fields['exports'].choice_validators = export_choices.get('choice_validators', {})
            self.fields['exports'].widget.choice_attributes = export_choices.get('choice_attributes', {})
            self.fields['exports'].widget.choice_warnings = export_choices.get('choice_warnings', {})

    new_repo = forms.BooleanField(
        label=_('Create new repository'),
        required=False,
        widget=forms.CheckboxInput(
            attrs={
                'onclick': f'''toggleRepoFields("id_new_repo", "form-group field-new_repo_name", "form-group field-repo")'''
        })
    )

    new_repo_name = forms.CharField(
        label=_('Name for the new repository'),
        help_text=_(
            'Unique name for the new repository. No other of your repositories may have the same name, otherwise the export will fail.'
        ),
        required=False,
        widget=forms.TextInput(attrs={'placeholder': _('example-repo-name')}),
        validators=[validate_new_repo_name]
    )

    repo = forms.ChoiceField(
        label=_('GitHub repository'),
        required=False,
        widget=forms.RadioSelect
    )

    exports = MultivalueCheckboxMultipleChoiceField(
        label=_('Export choices'),
        help_text=_('Warning: Existing content in GitHub will be overwritten.'),
        include_select_all_choice=True
    )

    branch = forms.CharField(
        label=_('Branch'), 
        help_text=_('An existing branch in the GitHub repository. For a new repository it must be the default branch "main".'),
        initial='main'
    )

    commit_message = forms.CharField(label=_('Commit message'))

    class Media:
        js = [format_html(
            '<script src="{}" repoToggleId="id_new_repo" checkedCollectionClass="form-group field-new_repo_name" uncheckedCollectionClass="form-group field-repo" ></script>',
            static('plugins/js/github_form.js')
        )]

    def clean(self):
        super().clean()
        new_repo = self.cleaned_data.get('new_repo')
        new_repo_name = self.cleaned_data.get('new_repo_name')
        repo = self.cleaned_data.get('repo')

        if new_repo and new_repo_name == '':
            self.add_error('new_repo_name', ValidationError(_('A name for the new repository is required.'), code='required'))
        
        if not new_repo and 'new_repo_name' in self.errors: # ignore new_repo_errors because repo will be used instead
            self._errors.pop('new_repo_name')

        if not new_repo and repo == '':
            self.add_error('repo', ValidationError(_('A GitHub repository is required.'), code='required'))

class GitHubImportForm(GithubBaseForm):
    def __init__(self, *args, **kwargs):
        repo_choices = kwargs.get('repo_choices')
        import_choices = kwargs.pop('import_choices', None)
        super().__init__(*args, **kwargs)

        if repo_choices is not None and len(repo_choices) == 0:
            self.fields['other_repo_check'].initial = True

        if import_choices is not None:
            self.fields['imports'].choices = import_choices.get('choices')
            self.fields['imports'].choice_validators = import_choices.get('choice_validators', {})
            self.fields['imports'].widget.choice_attributes = import_choices.get('choice_attributes', {})
            self.fields['imports'].widget.choice_warnings = import_choices.get('choice_warnings', {})
        else:
            self.fields['imports'] = forms.CharField(
                label=_('File path'),
                help_text=_("The import file's relative path in the repository. The file must be in XML format."),
                widget=forms.TextInput(attrs={'placeholder': _('example_folder/example_xml_file.xml')}),
                validators=[validate_import_file_path]
            )

    other_repo_check = forms.BooleanField(
        label=_('Use other repository'),
        required=False,
        widget=forms.CheckboxInput(
            attrs={'onclick': 'toggleRepoFields("id_other_repo_check", "form-group field-other_repo", "form-group field-repo")'}
        )
    )

    repo = forms.ChoiceField(
        label=_('GitHub repository'),
        required=False,
        widget=forms.RadioSelect
    )

    other_repo = forms.CharField(
        label=_('GitHub repository'),
        help_text=_(
            'URL of GitHub repository you want to import from. If this repository is not public, you must have access to it.'
        ),
        widget=forms.TextInput(attrs={'placeholder': _('https://github.com/example-owner/example-repo')}),
        required=False
    )

    imports = MultivalueCheckboxMultipleChoiceField(
        label=_('Import choices'),
        help_text=_('Select the choices you want to import from. Once they are in the gray box, move them to prioritize them.'),
        sortable=True
    )

    ref = forms.CharField(
        label=_('Branch, tag, or commit'), 
        initial='main'
    )

    class Media:
        js = [format_html(
            '<script src="{}" repoToggleId="id_other_repo_check" checkedCollectionClass="form-group field-other_repo" uncheckedCollectionClass="form-group field-repo" ></script>',
            static('plugins/js/github_form.js')
        )]

    def clean(self):
        super().clean()
        other_repo_check = self.cleaned_data.get('other_repo_check')
        other_repo = self.cleaned_data.get('other_repo')
        repo = self.cleaned_data.get('repo')

        if other_repo_check and other_repo == '':
            self.add_error('other_repo', ValidationError(_('A GitHub repository is required.'), code='required'))
        
        if not other_repo_check and repo == '':
            self.add_error('repo', ValidationError(_('A GitHub repository is required.'), code='required'))

    