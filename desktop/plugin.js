// grokbot-staff Desktop pane. Uses only @hermes/plugin-sdk and react.
import { host, useValue } from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'grokbot-staff'

function settingsHref() {
  return '/settings?tab=plugins&plugin=' + ID
}

function Card(props) {
  return jsxs('div', {
    className: 'rounded border p-2 ' + (props.alert ? 'border-amber-500' : 'border-(--ui-border)'),
    children: [
      jsx('div', { className: 'font-medium', children: props.title }),
      props.sub ? jsx('div', { className: 'whitespace-pre-line text-xs text-(--ui-text-tertiary)', children: props.sub }) : null
    ]
  })
}

const STATUS_LABEL = {
  running: 'running',
  done: 'done · verified + reviewed',
  needs_human: 'needs you',
  failed: 'failed'
}

function jobSub(j) {
  const parts = [j.specialist, STATUS_LABEL[j.status] || j.status]
  if (j.attempts) parts.push('attempt ' + j.attempts + (j.max_attempts ? '/' + j.max_attempts : ''))
  if (j.review_verdict) parts.push('review: ' + j.review_verdict)
  let sub = parts.join(' · ')
  if (j.status !== 'done' && j.last_feedback) sub += '\n' + j.last_feedback
  if (j.status === 'needs_human' && j.reason) sub += '\n' + j.reason + ' — /staff resume ' + j.id
  return sub
}

function StaffPane(props) {
  const ctx = props.ctx
  const gateway = useValue(host.state.gateway)
  const [data, setData] = useState(null)
  const [error, setError] = useState('')

  const load = () => {
    ctx.rest('/overview').then(
      d => { setData(d); setError('') },
      e => setError(String((e && e.message) || e || 'backend unavailable'))
    )
  }

  useEffect(() => {
    load()
    const stop = ctx.setInterval(load, 10000)
    return stop
  }, [gateway])

  if (error) {
    return jsx('div', { className: 'p-3 text-sm', children:
      'Staff backend not reachable. Enable the grokbot-staff agent plugin (hermes plugins enable grokbot-staff). ' + error })
  }
  if (!data) return jsx('div', { className: 'p-3 text-sm', children: 'Loading staff…' })

  const pending = data.jobs.filter(j => j.kind === 'grokbot' && j.status === 'pending')
  const recent = data.jobs.filter(j => j.kind === 'local').slice(0, 8)
  const waiting = data.jobs.filter(j => j.kind === 'local' && j.status === 'needs_human')
  const bridgeOn = data.bridge.webhook_configured || !!data.bridge.inbox_dir

  return jsxs('div', {
    className: 'flex h-full flex-col gap-3 overflow-auto p-3 text-sm',
    children: [
      jsx('div', { className: 'font-semibold', children: 'G.I.M.F.' }),
      jsx('div', { className: 'text-xs text-(--ui-text-tertiary)', children: 'Grok is my friend' }),
      data.loop
        ? jsx('div', { className: 'text-xs', children: data.loop.enabled
            ? 'Harness · Loop · Graph: on (max ' + data.loop.max_attempts + ' attempts, verifier + reviewer)'
            : 'Quality loop: off (single answer)' })
        : null,
      jsx('div', { className: 'font-semibold', children: 'Team' }),
      jsx('div', { className: 'grid gap-2', children:
        data.team.map(s => jsx(Card, { key: s.id, title: s.title + ' · ' + s.id, sub: s.focus })) }),
      waiting.length
        ? jsx('div', { className: 'font-semibold', children: 'Needs you (' + waiting.length + ')' })
        : null,
      waiting.length
        ? jsx('div', { className: 'grid gap-2', children:
            waiting.map(j => jsx(Card, { key: 'w' + j.id, alert: true, title: j.title || j.id, sub: jobSub(j) })) })
        : null,
      jsx('div', { className: 'font-semibold', children: 'Pending Grok Bot handoffs (' + pending.length + ')' }),
      pending.length
        ? jsx('div', { className: 'grid gap-2', children:
            pending.map(j => jsx(Card, { key: j.id, title: j.title || j.id, sub: j.id + ' · ' + j.created_at })) })
        : jsx('div', { className: 'text-xs text-(--ui-text-tertiary)', children: 'None.' }),
      jsx('div', { className: 'font-semibold', children: 'Recent staff jobs' }),
      recent.length
        ? jsx('div', { className: 'grid gap-2', children:
            recent.map(j => jsx(Card, { key: j.id, alert: j.status === 'needs_human', title: j.title || j.id, sub: jobSub(j) })) })
        : jsx('div', { className: 'text-xs text-(--ui-text-tertiary)', children: 'Try: /staff chief plan my launch week' }),
      jsxs('div', {
        className: 'mt-auto flex items-center justify-between text-xs',
        children: [
          jsx('span', { children: 'Grok Bot bridge: ' + (bridgeOn ? 'configured' : 'off') }),
          jsx('button', {
            type: 'button',
            className: 'underline',
            onClick: () => host.navigate(settingsHref()),
            children: 'Settings'
          })
        ]
      })
    ]
  })
}

export default {
  id: ID,
  name: 'G.I.M.F.',
  register(ctx) {
    ctx.register({
      id: 'pane',
      area: 'panes',
      title: 'G.I.M.F.',
      data: { placement: 'right', width: '300px' },
      render: () => jsx(StaffPane, { ctx })
    })
  }
}
