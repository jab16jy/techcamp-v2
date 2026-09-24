import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router'
import { Button } from '../../../design-system/ui/button'
import { Input } from '../../../design-system/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '../../../design-system/ui/select'
import { ApiError } from '../../../lib/api/client'
import { setOrgId, setSession } from '../../../lib/api/session'
import { fetchMe, requestOtp, verifyOtp, type Membership } from '../api/authApi'

const ROLE_LABELS: Record<string, string> = {
  owner: 'Propietario',
  technician: 'Técnico',
  producer: 'Productor',
  viewer: 'Observador',
}

/** docs/03-modelo-datos.md has no organization name field reachable from `GET
 * /me` (only `org_id`/`role` per membership) — flagged gap, not invented: the
 * chooser shows a shortened id plus role until an org-name endpoint exists. */
// eslint-disable-next-line react-refresh/only-export-components -- exported for a focused unit test
export function orgLabel(membership: Membership): string {
  const shortId = membership.org_id.slice(0, 8)
  const role = ROLE_LABELS[membership.role] ?? membership.role
  return `Organización ${shortId} · ${role}`
}

type Step = 'phone' | 'code' | 'org'

/** Minimal phone OTP sign-in (owner decision 2026-09-24): phone → code → org
 * pick when the user has more than one membership. Consent stays out of
 * scope. Spanish UI copy per docs/07. */
export function SignInScreen() {
  const navigate = useNavigate()
  const [step, setStep] = useState<Step>('phone')
  const [phone, setPhone] = useState('')
  const [code, setCode] = useState('')
  const [memberships, setMemberships] = useState<Membership[]>([])
  const [selectedOrgId, setSelectedOrgId] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleRequestOtp(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setLoading(true)
    try {
      await requestOtp(phone)
      setStep('code')
    } catch {
      setError('No pudimos enviar el código. Intenta de nuevo.')
    } finally {
      setLoading(false)
    }
  }

  async function handleVerify(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setLoading(true)
    try {
      const token = await verifyOtp(phone, code)
      setSession(token, null)
      const me = await fetchMe()
      if (me.memberships.length === 0) {
        setError('Tu cuenta no pertenece a ninguna organización todavía.')
        return
      }
      if (me.memberships.length === 1) {
        setOrgId(me.memberships[0].org_id)
        navigate('/', { replace: true })
        return
      }
      setMemberships(me.memberships)
      setSelectedOrgId(me.memberships[0].org_id)
      setStep('org')
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError('El código es incorrecto o venció.')
      } else {
        setError('No pudimos verificar el código. Intenta de nuevo.')
      }
    } finally {
      setLoading(false)
    }
  }

  function handleChooseOrg(event: FormEvent) {
    event.preventDefault()
    setOrgId(selectedOrgId)
    navigate('/', { replace: true })
  }

  return (
    <div className="flex min-h-dvh flex-col justify-center gap-6 px-4 py-8">
      <h1 className="font-serif text-2xl">Ingresar</h1>

      {step === 'phone' && (
        <form className="flex flex-col gap-4" onSubmit={handleRequestOtp}>
          <label className="flex flex-col gap-2 text-base" htmlFor="phone">
            Teléfono
            <Input
              id="phone"
              type="tel"
              autoComplete="tel"
              placeholder="3001234567"
              value={phone}
              onChange={(event) => setPhone(event.target.value)}
              required
            />
          </label>
          {error && (
            <p role="alert" className="text-base text-severity-critical">
              {error}
            </p>
          )}
          <Button type="submit" loading={loading}>
            Enviar código
          </Button>
        </form>
      )}

      {step === 'code' && (
        <form className="flex flex-col gap-4" onSubmit={handleVerify}>
          <p className="text-base text-text-muted">Te enviamos un código a {phone}.</p>
          <label className="flex flex-col gap-2 text-base" htmlFor="code">
            Código
            <Input
              id="code"
              inputMode="numeric"
              autoComplete="one-time-code"
              placeholder="123456"
              value={code}
              onChange={(event) => setCode(event.target.value)}
              required
            />
          </label>
          {error && (
            <p role="alert" className="text-base text-severity-critical">
              {error}
            </p>
          )}
          <Button type="submit" loading={loading}>
            Verificar
          </Button>
          <Button type="button" variant="ghost" onClick={() => setStep('phone')}>
            Cambiar número
          </Button>
        </form>
      )}

      {step === 'org' && (
        <form className="flex flex-col gap-4" onSubmit={handleChooseOrg}>
          <p className="text-base text-text-muted">Elige tu organización.</p>
          <Select value={selectedOrgId} onValueChange={setSelectedOrgId}>
            <SelectTrigger aria-label="Organización">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {memberships.map((membership) => (
                <SelectItem key={membership.org_id} value={membership.org_id}>
                  {orgLabel(membership)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button type="submit">Continuar</Button>
        </form>
      )}
    </div>
  )
}
