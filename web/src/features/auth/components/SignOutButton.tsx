import { useNavigate } from 'react-router'
import { Button } from '../../../design-system/ui/button'
import { clearSession } from '../../../lib/api/session'

export function SignOutButton() {
  const navigate = useNavigate()

  function handleSignOut() {
    clearSession()
    navigate('/ingreso', { replace: true })
  }

  return (
    <Button variant="secondary" onClick={handleSignOut}>
      Cerrar sesión
    </Button>
  )
}
