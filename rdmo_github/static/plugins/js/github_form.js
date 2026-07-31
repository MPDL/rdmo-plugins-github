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