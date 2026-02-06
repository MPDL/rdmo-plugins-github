function toggleRepoFields(checkbox_id, checked_collection_class, unchecked_collection_class, checked_submit_value, unchecked_submit_value) {
    const checkBox = document.getElementById(checkbox_id);
    let checkedCollection = document.getElementsByClassName(checked_collection_class);
    let uncheckedCollection = document.getElementsByClassName(unchecked_collection_class);
    let submitButton = document.getElementsByClassName('btn btn-primary');
    
    if (checkBox.checked == true){
        checkedCollection[0].style.display = 'block';
        uncheckedCollection[0].style.display = 'none';
        if (submitButton && checked_submit_value) {
            submitButton[0].value = checked_submit_value;
        }
    } else {
        checkedCollection[0].style.display = 'none';
        uncheckedCollection[0].style.display = 'block';
        if (submitButton && unchecked_submit_value) {
            submitButton[0].value = unchecked_submit_value;
        }
    }
}

function hideAllChoiceWarningMessages(text, choice_count) {
    let duration = 1000;
    clearTimeout(text._timer);
    text._timer = setTimeout(()=>{
        for (let i=0; i<choice_count; i++) {
            let choice_warning_messages = document.getElementById(`id_warnings_${i}`);
            if (choice_warning_messages) {
                choice_warning_messages.style.display = 'none';
            }
        }
    }, duration);
}