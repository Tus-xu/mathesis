/** 全局图片 lightbox（单例，点击遮罩/关闭按钮关闭，Esc 关闭） */
let lb = null
let escBound = false

export function openLightbox(src) {
  if (!src) return
  if (!lb) {
    lb = document.createElement('div')
    lb.className = 'md-lightbox'
    lb.innerHTML = '<img alt="" /><span class="lb-close">✕</span>'
    document.body.appendChild(lb)
    lb.addEventListener('click', () => lb.classList.remove('show'))
    if (!escBound) {
      escBound = true
      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && lb) lb.classList.remove('show')
      })
    }
  }
  lb.querySelector('img').src = src
  lb.classList.add('show')
}
