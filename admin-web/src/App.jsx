import { useEffect, useState } from 'react'
import { supabase, isConfigured, MANAGE_ROLES } from './lib/supabase'
import './App.css'

const ROLES = ['admin', 'quality_manager', 'operator', 'analyst', 'technician']

export default function App() {
  const [session, setSession] = useState(null)
  const [loading, setLoading] = useState(true)
  const [orgs, setOrgs] = useState([])
  const [activeOrg, setActiveOrg] = useState(null)
  const [refreshKey, setRefreshKey] = useState(0)
  const [recovery, setRecovery] = useState(false)

  useEffect(() => {
    if (!isConfigured) {
      setLoading(false)
      return
    }
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session)
      setLoading(false)
    })
    const { data: sub } = supabase.auth.onAuthStateChange((event, next) => {
      if (event === 'PASSWORD_RECOVERY') setRecovery(true)
      setSession(next)
    })
    return () => sub.subscription.unsubscribe()
  }, [])

  useEffect(() => {
    if (!session) {
      setOrgs([])
      setActiveOrg(null)
      return
    }
    let cancelled = false
    ;(async () => {
      await supabase.rpc('claim_invitations')
      const { data } = await supabase.from('my_orgs').select('*').order('name')
      if (cancelled) return
      setOrgs(data ?? [])
      setActiveOrg((previous) =>
        (data ?? []).find((o) => o.id === previous?.id) ?? data?.[0] ?? null,
      )
    })()
    return () => {
      cancelled = true
    }
  }, [session, refreshKey])

  if (!isConfigured) {
    return (
      <div className="center">
        <div className="card">
          <h1>VisionQC Admin</h1>
          <p className="muted">
            Missing configuration. Run{' '}
            <code>python3 tools/write_supabase_env.py --hosted</code> (or without
            <code> --hosted</code> for the local stack) and restart the dev server.
          </p>
        </div>
      </div>
    )
  }

  if (loading) return <div className="center muted">Loading…</div>
  if (recovery) return <SetPassword onDone={() => setRecovery(false)} />
  if (!session) return <SignIn />
  if (orgs.length === 0) {
    return <NoAccess session={session} onCreated={() => setRefreshKey((k) => k + 1)} />
  }

  return (
    <Console
      session={session}
      orgs={orgs}
      org={activeOrg}
      onOrgChange={setActiveOrg}
      onRefresh={() => setRefreshKey((k) => k + 1)}
    />
  )
}

function SignIn() {
  const [mode, setMode] = useState('signin') // signin | signup | forgot
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [name, setName] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    setNotice('')
    try {
      if (mode === 'forgot') {
        const { error: resetError } = await supabase.auth.resetPasswordForEmail(
          email,
          { redirectTo: window.location.origin },
        )
        if (resetError) throw resetError
        setNotice('If that email exists, a reset link is on its way.')
        return
      }
      if (mode === 'signup') {
        const { error: signUpError } = await supabase.auth.signUp({
          email,
          password,
          options: { data: { full_name: name } },
        })
        if (signUpError) throw signUpError
      }
      const { error: signInError } = await supabase.auth.signInWithPassword({
        email,
        password,
      })
      if (signInError) throw signInError
    } catch (err) {
      setError(err.message ?? String(err))
    } finally {
      setBusy(false)
    }
  }

  const titles = {
    signin: ['VisionQC Admin', 'Organization access console'],
    signup: ['Create your account', 'The first user creates the organization'],
    forgot: ['Reset your password', 'We will email you a reset link'],
  }

  return (
    <div className="center">
      <form className="card auth" onSubmit={submit}>
        <h1>{titles[mode][0]}</h1>
        <p className="muted">{titles[mode][1]}</p>
        {mode === 'signup' && (
          <label>
            Full name
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </label>
        )}
        <label>
          Email
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
        </label>
        {mode !== 'forgot' && (
          <label>
            Password
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </label>
        )}
        {error && <p className="error">{error}</p>}
        {notice && <p className="muted">{notice}</p>}
        <button className="primary" disabled={busy}>
          {mode === 'signin' && 'Sign in'}
          {mode === 'signup' && 'Create account'}
          {mode === 'forgot' && 'Send reset link'}
        </button>
        <div className="auth-links">
          {mode === 'signin' && (
            <>
              <button type="button" className="link" onClick={() => setMode('signup')}>
                First time? Create an account
              </button>
              <button type="button" className="link" onClick={() => setMode('forgot')}>
                Forgot password?
              </button>
            </>
          )}
          {mode !== 'signin' && (
            <button type="button" className="link" onClick={() => setMode('signin')}>
              Back to sign in
            </button>
          )}
        </div>
      </form>
    </div>
  )
}

function SetPassword({ onDone }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function save(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    const { error: updateError } = await supabase.auth.updateUser({ password })
    setBusy(false)
    if (updateError) {
      setError(updateError.message)
      return
    }
    onDone()
  }

  return (
    <div className="center">
      <form className="card auth" onSubmit={save}>
        <h1>Set a new password</h1>
        <p className="muted">Choose a new password for your account.</p>
        <label>
          New password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            minLength={6}
            required
          />
        </label>
        {error && <p className="error">{error}</p>}
        <button className="primary" disabled={busy || password.length < 6}>
          Save password
        </button>
      </form>
    </div>
  )
}

function NoAccess({ session, onCreated }) {
  const [name, setName] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState('Waiting for an invitation — checking automatically.')

  async function check(silent = false) {
    if (!silent) setStatus('Checking for invitations…')
    await supabase.rpc('claim_invitations')
    const { data } = await supabase.from('my_orgs').select('*')
    if (data?.length) {
      onCreated()
      return
    }
    if (!silent) setStatus('Still no access. Ask your admin to invite you.')
  }

  useEffect(() => {
    const timer = setInterval(() => check(true), 10000)
    return () => clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function create(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    const { error: rpcError } = await supabase.rpc('create_org', { org_name: name })
    setBusy(false)
    if (rpcError) {
      setError(rpcError.message)
      return
    }
    onCreated()
  }

  return (
    <div className="center">
      <div className="card auth">
        <h1>No access yet</h1>
        <p className="muted">
          Signed in as {session.user.email}. This account is not a member of any
          organization.
        </p>
        <p className="muted">{status}</p>
        <button className="primary" onClick={() => check(false)}>
          Check again
        </button>
        <details>
          <summary>Create a new organization instead</summary>
          <form className="auth" onSubmit={create} style={{ marginTop: 12 }}>
            <label>
              Organization name
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
              />
            </label>
            {error && <p className="error">{error}</p>}
            <button className="primary" disabled={busy || name.trim().length < 2}>
              Create organization
            </button>
          </form>
        </details>
        <button type="button" className="link" onClick={() => supabase.auth.signOut()}>
          Sign out
        </button>
      </div>
    </div>
  )
}

function Console({ session, orgs, org, onOrgChange, onRefresh }) {
  const [tab, setTab] = useState('overview')
  const canManage = org && MANAGE_ROLES.includes(org.role)

  const tabs = [
    ['overview', 'Overview'],
    ['members', 'Members'],
    ['invitations', 'Invitations'],
    ['cameras', 'Cameras'],
    ['settings', 'Settings'],
  ]

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">VisionQC Admin</div>
        {orgs.length > 1 ? (
          <select
            value={org?.id ?? ''}
            onChange={(e) =>
              onOrgChange(orgs.find((o) => o.id === e.target.value) ?? org)
            }
          >
            {orgs.map((o) => (
              <option key={o.id} value={o.id}>
                {o.name}
              </option>
            ))}
          </select>
        ) : (
          <div className="org-name">{org?.name}</div>
        )}
        <div className="role">Role: {org?.role}</div>
        <nav>
          {tabs.map(([key, label]) => (
            <button
              key={key}
              className={tab === key ? 'nav active' : 'nav'}
              onClick={() => setTab(key)}
            >
              {label}
            </button>
          ))}
        </nav>
        <div className="spacer" />
        <div className="email">{session.user.email}</div>
        <button className="nav" onClick={() => supabase.auth.signOut()}>
          Sign out
        </button>
      </aside>

      <main className="content">
        {tab === 'overview' && <Overview org={org} />}
        {tab === 'members' && (
          <Members org={org} canManage={canManage} onChanged={onRefresh} />
        )}
        {tab === 'invitations' && (
          <Invitations org={org} session={session} canManage={canManage} />
        )}
        {tab === 'cameras' && <Cameras org={org} canManage={canManage} />}
        {tab === 'settings' && (
          <SettingsTab org={org} canManage={canManage} onChanged={onRefresh} />
        )}
      </main>
    </div>
  )
}

function useLoad(loader, deps) {
  const [state, setState] = useState({ loading: true, data: null, error: '' })
  useEffect(() => {
    let cancelled = false
    setState((s) => ({ ...s, loading: true }))
    loader()
      .then((data) => !cancelled && setState({ loading: false, data, error: '' }))
      .catch((err) =>
        !cancelled &&
        setState({ loading: false, data: null, error: err.message ?? String(err) }),
      )
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  return state
}

function Overview({ org }) {
  const { data, loading } = useLoad(async () => {
    const counts = {}
    for (const table of ['memberships', 'cameras', 'inspections', 'product_profiles']) {
      const { count } = await supabase
        .from(table)
        .select('*', { count: 'exact', head: true })
        .eq('org_id', org.id)
      counts[table] = count ?? 0
    }
    return counts
  }, [org.id])

  if (loading) return <p className="muted">Loading…</p>
  const tiles = [
    ['Members', data?.memberships],
    ['Cameras', data?.cameras],
    ['Inspections', data?.inspections],
    ['Product profiles', data?.product_profiles],
  ]
  return (
    <section>
      <h2>{org.name}</h2>
      <p className="muted">
        Manage who can access VisionQC and how the factory is organized.
      </p>
      <div className="tiles">
        {tiles.map(([label, value]) => (
          <div key={label} className="tile">
            <div className="tile-value">{value}</div>
            <div className="muted">{label}</div>
          </div>
        ))}
      </div>
    </section>
  )
}

function Members({ org, canManage, onChanged }) {
  const [version, setVersion] = useState(0)
  const { data, loading, error } = useLoad(async () => {
    const { data: memberships, error: membershipError } = await supabase
      .from('memberships')
      .select('id, role, user_id, created_at')
      .eq('org_id', org.id)
    if (membershipError) throw membershipError
    const ids = (memberships ?? []).map((m) => m.user_id)
    if (ids.length === 0) return []
    const { data: profiles, error: profileError } = await supabase
      .from('profiles')
      .select('id, email, full_name')
      .in('id', ids)
    if (profileError) throw profileError
    const byId = Object.fromEntries((profiles ?? []).map((p) => [p.id, p]))
    return (memberships ?? []).map((m) => ({ ...m, profile: byId[m.user_id] }))
  }, [org.id, version])

  async function changeRole(id, role) {
    const { error: updateError } = await supabase
      .from('memberships')
      .update({ role })
      .eq('id', id)
    if (updateError) alert(updateError.message)
    setVersion((v) => v + 1)
    onChanged?.()
  }

  async function remove(row) {
    if (!confirm(`Remove ${row.profile?.email ?? row.user_id} from ${org.name}?`)) return
    const { error: deleteError } = await supabase
      .from('memberships')
      .delete()
      .eq('id', row.id)
    if (deleteError) alert(deleteError.message)
    setVersion((v) => v + 1)
    onChanged?.()
  }

  if (loading) return <p className="muted">Loading…</p>
  if (error) return <p className="error">{error}</p>

  return (
    <section>
      <h2>Members</h2>
      <p className="muted">
        {canManage
          ? 'Change roles or remove access. Only owners and admins can edit.'
          : 'Read-only: only owners and admins can change access.'}
      </p>
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Email</th>
            <th>Role</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {data?.map((row) => (
            <tr key={row.id}>
              <td>{row.profile?.full_name || '—'}</td>
              <td>{row.profile?.email ?? row.user_id}</td>
              <td>
                {canManage && row.role !== 'owner' ? (
                  <select
                    value={row.role}
                    onChange={(e) => changeRole(row.id, e.target.value)}
                  >
                    {ROLES.map((role) => (
                      <option key={role} value={role}>
                        {role}
                      </option>
                    ))}
                  </select>
                ) : (
                  row.role
                )}
              </td>
              <td>
                {canManage && row.role !== 'owner' && (
                  <button className="danger" onClick={() => remove(row)}>
                    Remove
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

function Invitations({ org, session, canManage }) {
  const [version, setVersion] = useState(0)
  const [email, setEmail] = useState('')
  const [role, setRole] = useState('operator')
  const [message, setMessage] = useState('')
  const { data, loading, error } = useLoad(async () => {
    const { data: rows, error: listError } = await supabase
      .from('invitations')
      .select('*')
      .eq('org_id', org.id)
      .order('created_at', { ascending: false })
    if (listError) throw listError
    return rows
  }, [org.id, version])

  async function invite(event) {
    event.preventDefault()
    setMessage('')
    const { error: insertError } = await supabase.from('invitations').insert({
      org_id: org.id,
      email: email.trim(),
      role,
      invited_by: session.user.id,
    })
    if (insertError) {
      setMessage(insertError.message)
      return
    }
    setEmail('')
    setMessage('Invitation created. The user is granted access on next sign-in.')
    setVersion((v) => v + 1)
  }

  async function revoke(id) {
    const { error: deleteError } = await supabase
      .from('invitations')
      .delete()
      .eq('id', id)
    if (deleteError) alert(deleteError.message)
    setVersion((v) => v + 1)
  }

  if (loading) return <p className="muted">Loading…</p>
  if (error) return <p className="error">{error}</p>

  return (
    <section>
      <h2>Invitations</h2>
      {canManage ? (
        <form className="row" onSubmit={invite}>
          <input
            type="email"
            placeholder="name@factory.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
          <select value={role} onChange={(e) => setRole(e.target.value)}>
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
          <button className="primary">Invite</button>
        </form>
      ) : (
        <p className="muted">Only owners and admins can invite members.</p>
      )}
      {message && <p className="muted">{message}</p>}
      <table>
        <thead>
          <tr>
            <th>Email</th>
            <th>Role</th>
            <th>Status</th>
            <th>Expires</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {data?.map((row) => (
            <tr key={row.id}>
              <td>{row.email}</td>
              <td>{row.role}</td>
              <td>{row.accepted_at ? 'accepted' : 'pending'}</td>
              <td>{row.expires_at?.slice(0, 16).replace('T', ' ')}</td>
              <td>
                {canManage && !row.accepted_at && (
                  <button className="danger" onClick={() => revoke(row.id)}>
                    Revoke
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

function Cameras({ org, canManage }) {
  const [version, setVersion] = useState(0)
  const [name, setName] = useState('')
  const [kind, setKind] = useState('rtsp')
  const [address, setAddress] = useState('')
  const { data, loading, error } = useLoad(async () => {
    const { data: rows, error: listError } = await supabase
      .from('cameras')
      .select('*')
      .eq('org_id', org.id)
      .order('created_at')
    if (listError) throw listError
    return rows
  }, [org.id, version])

  async function add(event) {
    event.preventDefault()
    const { error: insertError } = await supabase.from('cameras').insert({
      org_id: org.id,
      name: name.trim(),
      kind,
      address: address.trim() || '0',
    })
    if (insertError) alert(insertError.message)
    setName('')
    setAddress('')
    setVersion((v) => v + 1)
  }

  async function remove(id) {
    if (!confirm('Delete this camera? Past inspections are kept.')) return
    const { error: deleteError } = await supabase.from('cameras').delete().eq('id', id)
    if (deleteError) alert(deleteError.message)
    setVersion((v) => v + 1)
  }

  if (loading) return <p className="muted">Loading…</p>
  if (error) return <p className="error">{error}</p>

  return (
    <section>
      <h2>Cameras</h2>
      <p className="muted">
        Line cameras registered here appear on the desktop app. Live preview and
        inference stay on the factory machine.
      </p>
      {canManage && (
        <form className="row" onSubmit={add}>
          <input
            placeholder="Camera name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="rtsp">RTSP / IP camera</option>
            <option value="usb">USB / built-in</option>
            <option value="mobile">Mobile (paired)</option>
          </select>
          <input
            placeholder="rtsp://… or index"
            value={address}
            onChange={(e) => setAddress(e.target.value)}
          />
          <button className="primary">Add</button>
        </form>
      )}
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Type</th>
            <th>Address</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {data?.map((row) => (
            <tr key={row.id}>
              <td>{row.name}</td>
              <td>{row.kind}</td>
              <td>{row.address}</td>
              <td>
                {canManage && (
                  <button className="danger" onClick={() => remove(row.id)}>
                    Delete
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

function SettingsTab({ org, canManage, onChanged }) {
  const [name, setName] = useState(org.name)
  const [message, setMessage] = useState('')

  async function save(event) {
    event.preventDefault()
    setMessage('')
    const { error } = await supabase
      .from('orgs')
      .update({ name: name.trim() })
      .eq('id', org.id)
    if (error) {
      setMessage(error.message)
      return
    }
    setMessage('Saved.')
    onChanged?.()
  }

  return (
    <section>
      <h2>Settings</h2>
      <p className="muted">
        Organization slug: <code>{org.slug}</code>
      </p>
      {canManage ? (
        <form className="row" onSubmit={save}>
          <input value={name} onChange={(e) => setName(e.target.value)} required />
          <button className="primary">Save</button>
        </form>
      ) : (
        <p className="muted">Only owners and admins can edit organization settings.</p>
      )}
      {message && <p className="muted">{message}</p>}
    </section>
  )
}
