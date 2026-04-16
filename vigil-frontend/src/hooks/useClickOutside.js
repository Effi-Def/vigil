import { useEffect } from 'react'

export default function useClickOutside(ref, onOutside) {
  useEffect(() => {
    function handlePointerDown(event) {
      const node = ref?.current
      if (!node) return
      if (node.contains(event.target)) return
      if (onOutside) onOutside(event)
    }

    document.addEventListener('mousedown', handlePointerDown)
    document.addEventListener('touchstart', handlePointerDown, { passive: true })
    return () => {
      document.removeEventListener('mousedown', handlePointerDown)
      document.removeEventListener('touchstart', handlePointerDown)
    }
  }, [ref, onOutside])
}
