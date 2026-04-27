const repoToggleId = document.currentScript.getAttribute('repoToggleId')
const checkedCollectionClass = document.currentScript.getAttribute('checkedCollectionClass')
const uncheckedCollectionClass = document.currentScript.getAttribute('uncheckedCollectionClass')

document.addEventListener('DOMContentLoaded', () => {
    if (repoToggleId && checkedCollectionClass && uncheckedCollectionClass) {
        toggleRepoFields(repoToggleId, checkedCollectionClass, uncheckedCollectionClass)
    }
})

function toggleRepoFields(checkbox_id, checked_collection_class, unchecked_collection_class) {
    const checkBox = document.getElementById(checkbox_id)
    let checkedCollection = document.getElementsByClassName(checked_collection_class)
    let uncheckedCollection = document.getElementsByClassName(unchecked_collection_class)
    
    if (checkBox.checked == true){
        checkedCollection[0].style.display = 'block'
        uncheckedCollection[0].style.display = 'none'
    } else {
        checkedCollection[0].style.display = 'none'
        uncheckedCollection[0].style.display = 'block'
    }
}

function hideAllChoiceWarningMessages(text, choice_count) {
    let duration = 1000
    clearTimeout(text._timer)
    text._timer = setTimeout(()=>{
        for (let i=0; i<choice_count; i++) {
            let choice_warning_messages = document.getElementById(`id_warnings_${i}`)
            if (choice_warning_messages) {
                choice_warning_messages.style.display = 'none'
            }
        }
    }, duration)
}