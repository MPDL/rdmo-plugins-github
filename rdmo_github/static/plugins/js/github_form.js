const cbId = document.currentScript.getAttribute('cbId')
const checkedClass = document.currentScript.getAttribute('checkedClass')
const uncheckedClass = document.currentScript.getAttribute('uncheckedClass')

document.addEventListener('DOMContentLoaded', () => {
  if (cbId && checkedClass && uncheckedClass) {
    toggleRepoFields(cbId, checkedClass, uncheckedClass)
  }
})

function toggleRepoFields(cbId, checkedClass, uncheckedClass) {
  const checkBox = document.getElementById(cbId)
  let checkedCollection = document.getElementsByClassName(checkedClass)
  let uncheckedCollection = document.getElementsByClassName(uncheckedClass)
    
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