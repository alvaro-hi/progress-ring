import { describe, expect, mock, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { On } from 'claude-code'

import { pct, pickMine, stepRows, toFicha, validFiche } from '../hooks/lib'
import { attention, countsAt, isProgressOutput, milestoneOf, parseCalls, ringLabel, stepWindow, tokText } from '../hooks/view'
import { DICTS, envLocale, pickLang } from '../hooks/i18n'
import type { Snap } from '../types'

const NOW = Date.parse('2026-10-09T17:00:00+02:00')
const DIR = '/h/.claude/progress'
const SID = 'sess-1'
const SURFACES = ['terminal', 'desktop'] as const
const ES = DICTS.es
const EN = DICTS.en
/** Los textos de estos tests están en español: el ajuste `language` lo fija (en `auto`, sin LANG, saldría en inglés). */
const IN_ES = { options: { language: 'es' } } as const
const IN_EN = { options: { language: 'en' } } as const

/** Argumentos de una llamada al CLI, que viaja dentro del plugin (`<raíz>/bin/progress`); otra ruta queda entera. */
const cliArgs = (argv: string[] | undefined) => (argv?.[0] === 'python3' && argv[1] === 'bin/progress' ? argv.slice(2) : argv)

const ago = (min: number) => new Date(NOW - min * 60_000).toISOString()

function fiche(id: string, over: Record<string, unknown> = {}) {
  return {
    id,
    title: `Plan ${id}`,
    cwd: '/proj',
    done: 4,
    total: 10,
    iter: null,
    step: 'API leída',
    status: 'active',
    started: ago(60),
    updated: ago(3),
    keys: [],
    children: {},
    ...over,
  }
}

/** Plan de 4: uno cerrado hace 5 min, «Escribir el mod» en curso y 2 sin nombre. */
const PLANNED = {
  keys: ['api'],
  done: 1,
  total: 4,
  steps: [{ key: 'api', label: '', at: ago(5) }],
  plan: [
    { key: 'api', label: 'Leer la API' },
    { key: 'mod', label: 'Escribir el mod' },
  ],
}

/** Plan de 4 todo con nombre, a falta de dos. */
const NEAR_END = {
  keys: ['a', 'b'],
  done: 2,
  total: 4,
  steps: [
    { key: 'a', label: '', at: ago(50) },
    { key: 'b', label: '', at: ago(3) },
  ],
  plan: [
    { key: 'a', label: 'Leer' },
    { key: 'b', label: 'Escribir' },
    { key: 'c', label: 'Probar' },
    { key: 'd', label: 'Instalar' },
  ],
}

/** 40 lotes con 22 cerrados: ventana de cinco y lo demás plegado. */
const LOTES = {
  keys: Array.from({ length: 22 }, (_, i) => `l${i + 1}`),
  done: 22,
  total: 40,
  plan: Array.from({ length: 40 }, (_, i) => ({ key: `l${i + 1}`, label: `Lote ${i + 1}` })),
}

function filesOf(...fs: ReturnType<typeof fiche>[]): Map<string, string> {
  return new Map(fs.map(f => [`${DIR}/${f.id}.json`, JSON.stringify(f)]))
}

/** El mundo bajo el plugin: reloj, HOME, sesión, directorio de fichas en memoria y el motor de dibujo. */
function world(on: On, files: Map<string, string>, env: Record<string, string> = {}, hookSays = '') {
  const clock = mock.clock(on, { now: NOW })
  mock.env(on, { HOME: '/h', ...env })
  const runs: string[][] = []
  const opened: string[] = []
  const copied: string[] = []
  on('session.id', () => ({ value: SID }))
  on('session.cwd', () => ({ value: '/proj/sub' }))
  on('session.start', ($, e) => ({ cwd: e.cwd }))
  on('command.register', ($, e) => ({ value: { command: e.name } }))
  on('fs.stat', ($, e) => ({ value: { kind: 'dir' as const, size: 0, mtimeMs: 0, isLink: false, realPath: e.path } }))
  on('fs.exists', ($, e) => ({ value: e.path === DIR || [...files.keys()].some(p => p === e.path || p.startsWith(`${e.path}/`)) }))
  on('fs.list', ($, e) => ({
    value: [...files.keys()]
      .filter(p => p.slice(0, p.lastIndexOf('/')) === e.path)
      .map(p => ({ name: p.slice(e.path.length + 1), kind: 'file' as const, size: 1, mtimeMs: 0, isLink: false })),
  }))
  on('fs.read', ($, e) => {
    const text = files.get(e.path)
    if (text === undefined) throw new Error(`ENOENT ${e.path}`)
    return { value: text }
  })
  on('ui.toast', () => ({ value: undefined }))
  on('ui.copy', ($, e) => {
    copied.push(e.text)
    return { value: { isCopied: true } as never }
  })
  on('ui.open', ($, e) => {
    opened.push(e.id)
    return { value: { isPlaced: true as const } }
  })
  on('process.run', ($, e) => {
    runs.push([...e.argv])
    const stdout = e.argv[1] === 'bin/progress_hook.py' ? hookSays
      : e.argv[2] === 'chat' && e.argv[3] === 'term' ? 'claude --resume 460832df-56c9-4b74-aa1b-e720a7e1bc62\n' : ''
    return { value: { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('prompt.submit', ($, e) => ({ text: e.text }))
  on('ui.render', { component: 'AbovePrompt' }, ($, e) => {
    const { Text } = $.ui.resolve(e)
    return <Text>engine</Text>
  })
  on('ui.render', { component: 'ToolUse' }, ($, e) => {
    const { Text } = $.ui.resolve(e)
    return <Text>engine</Text>
  })
  on('ui.render', { component: 'ToolResult' }, ($, e) => {
    const { Text } = $.ui.resolve(e)
    return <Text>engine</Text>
  })
  on('ui.render', { component: 'ToolGroup' }, ($, e) => {
    const { Text } = $.ui.resolve(e)
    return <Text>{`grupo desplegado: ${e.props.isExpanded}`}</Text>
  })
  return { clock, runs, opened, copied }
}

const start = ($: Engine) => $.session.start({ cwd: '/proj/sub', surface: 'terminal', isInteractive: true })
const submit = ($: Engine) => $.prompt.submit({ text: 'siguiente', wait: false, origin: { kind: 'composer' } })

const BAND = {
  plugin: 'progress-ring',
  component: 'AbovePrompt',
  props: { hasSurvey: false, isWorking: false, maxRows: 20, bodyColumns: 100, scroll: { offset: 0, bodyRows: 20 }, view: {} },
} as const
const WORKING = { ...BAND, props: { ...BAND.props, isWorking: true } } as const

const PANE = {
  plugin: 'progress-ring',
  component: 'Pane',
  requestId: 'progress',
  props: { title: 'Progreso', isFocused: true, bodyColumns: 60, placement: 'dock', scroll: { offset: 0, bodyRows: 40 }, view: {} },
} as const

const out = (stdout: string) => ({ stdout, stderr: '', interrupted: false })
const toolUse = (command: string, stdout: string) =>
  ({
    plugin: 'progress-ring',
    component: 'ToolUse',
    requestId: 't1',
    props: { tool_use_id: 't1', tool: 'Bash', input: { command }, isRunning: false, isErrored: false, isInterrupted: false, output: out(stdout) },
  }) as const

async function texts(ui: { findAll: (q: { type: string }) => Promise<{ text: string }[]> }) {
  return (await ui.findAll({ type: 'Text' })).map(t => t.text).join('\n')
}

const snapOf = (...fs: ReturnType<typeof fiche>[]): Snap =>
  ({ fichas: fs.map(f => toFicha(f, ES)), bad: [], discreet: false, sessionId: SID, cwd: '/proj/sub', now: NOW, working: [] }) as Snap

describe('lógica', () => {
  test('el % redondea a la par y una ficha cuyo id no es su archivo no vale', () => {
    expect(pct(toFicha(fiche('a', { done: 1, total: 8 }), ES))).toBe(12)
    expect(validFiche(fiche('a'), 'a')).toBe(true)
    expect(validFiche(fiche('a'), 'b')).toBe(false)
    expect(validFiche(fiche('a', { steps: 'no' }), 'a')).toBe(false)
  })

  test('la de la sesión gana a la de la carpeta', () => {
    const s = snapOf(fiche('far', { cwd: '/proj' }), fiche('own', { cwd: '/otra', owner: SID }))
    expect(pickMine(s)?.id).toBe('own')
  })

  test('lista de pasos: cerrados con su hora, pendientes del plan y lo que falta sin nombre', () => {
    expect(stepRows(fiche('p', PLANNED), ES).map(r => [r.isDone, r.label])).toEqual([
      [true, 'Leer la API'],
      [false, 'Escribir el mod'],
      [false, '2 pasos pendientes sin nombre'],
    ])
  })

  test('hasta 7 pasos se ven todos; con más, dos antes, el actual y dos después', () => {
    expect(stepWindow(toFicha(fiche('p', PLANNED), ES), false).shown).toHaveLength(3)
    const w = stepWindow(toFicha(fiche('l', LOTES), ES), false)
    expect([w.before.length, w.shown.map(r => r.label), w.after.length]).toEqual([20, ['Lote 21', 'Lote 22', 'Lote 23', 'Lote 24', 'Lote 25'], 15])
    expect(stepWindow(toFicha(fiche('l', LOTES), ES), true).shown).toHaveLength(40)
  })

  test('agentes: solo cuenta lo que pide atención', () => {
    const kids = { a: { label: 'x', state: 'running', started: ago(70) }, b: { label: 'y', state: 'running', started: ago(5) }, c: { label: 'z', state: 'delivered', started: ago(9) } }
    expect(attention(toFicha(fiche('k', { children: kids }), ES), NOW, ES)).toBe('1 agente lleva más de 1 h · 1 entrega por revisar')
    expect(ringLabel(toFicha(fiche('big', { done: 128, total: 200 }), ES))).toBe('128')
  })

  test('lee las llamadas a progress, también vía variable, y la salida del CLI', () => {
    expect(parseCalls('~/.claude/bin/progress step mio "Leer la API" --key api')).toEqual([{ verb: 'step', id: 'mio', label: 'Leer la API', key: 'api' }])
    expect(parseCalls('P=~/.claude/bin/progress; $P step mio "X" --key k && $P done mio').map(c => c.verb)).toEqual(['step', 'done'])
    expect(parseCalls('progress child accept mio a-1')[0]?.key).toBe('child:a-1')
    expect(countsAt('mio · 1/4 · 25%\nmio · 2/4 · 50%', 'mio')).toEqual({ done: 2, total: 4 })
    expect(isProgressOutput('mio · 1/4 · 25%\n')).toBe(true)
    expect(isProgressOutput('mio · 1/4 · 25%\nOtra cosa')).toBe(false)
  })

  test('el hito dice «ahora» solo en el último; los anteriores, lo que vino luego', () => {
    const now = snapOf(fiche('mio', PLANNED))
    const latest = milestoneOf('progress step mio "Leer la API" --key api', 'mio · 1/4 · 25%', now, ES)
    expect([latest?.isLatest, latest?.closed, latest?.next]).toEqual([true, 'Leer la API', 'Escribir el mod'])
    const later = snapOf(fiche('mio', { ...PLANNED, keys: ['api', 'mod'], done: 2, steps: [...PLANNED.steps, { key: 'mod', label: '', at: ago(1) }] }))
    const old = milestoneOf('progress step mio "Leer la API" --key api', 'mio · 1/4 · 25%', later, ES)
    expect([old?.isLatest, old?.next]).toEqual([false, 'Escribir el mod'])
  })
})

describe('banda', () => {
  test('trabajando: el paso en curso arriba, el plan y el pulso debajo; anillo SVG o glifo', IN_ES, async ($, on) => {
    world(on, filesOf(fiche('mio', { owner: SID, ...PLANNED })))
    await start($)
    for (const surface of SURFACES) {
      const ui = await $.ui.mount({ ...WORKING, surface })
      const all = await texts(ui)
      expect(all).toContain('Escribir el mod')
      expect(all).toContain('Plan mio · ✓ Leer la API · hace 5 min')
      expect(all).not.toContain('paso 2 de 4')
      if (surface === 'terminal') expect(all).toContain('◔ 1/4')
      else expect(await ui.find({ type: 'Svg' })).toBeDefined()
      await ui.unmount()
    }
  })

  test('en reposo, sin turno ni agentes, espera tu respuesta', IN_ES, async ($, on) => {
    world(on, filesOf(fiche('mio', { owner: SID, ...PLANNED }), fiche('otro', { cwd: '/otra', owner: 'x' })))
    await start($)
    expect(await texts(await $.ui.mount({ ...BAND, surface: 'terminal' }))).toContain('esperando tu respuesta · ahora: Escribir el mod')
  })

  test('con un agente en marcha y sin turno, la banda trabaja y lo cuenta', IN_ES, async ($, on) => {
    const kids = { a: { label: 'buscar', state: 'running', started: ago(2) } }
    world(on, filesOf(fiche('mio', { owner: SID, ...PLANNED, children: kids })))
    await start($)
    const all = await texts(await $.ui.mount({ ...BAND, surface: 'terminal' }))
    expect(all).toContain('· 1 agente')
    expect(all).not.toContain('esperando tu respuesta')
  })

  test('parada: qué paso y cuánto, solo si Claude no trabaja; sin botones', IN_ES, async ($, on) => {
    world(on, filesOf(fiche('mio', { owner: SID, ...NEAR_END, updated: ago(20) })))
    await start($)
    const idle = await $.ui.mount({ ...BAND, surface: 'terminal' })
    expect(await texts(idle)).toContain('Probar lleva 20 min sin avance')
    expect(await idle.find({ key: 'pausar' })).toBeUndefined()
    await idle.unmount()
    const busy = await $.ui.mount({ ...WORKING, surface: 'terminal' })
    expect(await texts(busy)).not.toContain('sin avance')
  })

  test('fijada: la cabecera es el plan y el paso actual vive solo en la lista', IN_ES, async ($, on) => {
    world(on, filesOf(fiche('mio', { owner: SID, ...PLANNED })))
    await start($)
    const ui = await $.ui.mount({ ...WORKING, surface: 'desktop' })
    await ui.press({ key: 'fijar' })
    const all = await texts(ui)
    expect(all.split('\n').filter(l => l === 'Escribir el mod')).toHaveLength(1)
    expect(all).toContain('Plan mio')
    expect(all).toContain('Leer la API')
    expect(await ui.find({ key: 'fin-mio' })).toBeUndefined() // ni Pausar ni Terminar: eso es del chat
    expect(await ui.find({ key: 'pausa-mio' })).toBeUndefined()
  })

  test('el modo discreto esconde título y pasos', IN_ES, async ($, on) => {
    const files = filesOf(fiche('secreto', { owner: SID, ...NEAR_END }))
    files.set(`${DIR}/_config.json`, '{"discreet": true}')
    world(on, files)
    await start($)
    const all = await texts(await $.ui.mount({ ...WORKING, surface: 'terminal' }))
    expect(all).toContain('Actividad')
    expect(all).not.toContain('Plan secreto')
    expect(all).not.toContain('Probar')
  })

  test('al terminar, el cierre se queda hasta el siguiente prompt', IN_ES, async ($, on) => {
    const files = filesOf(fiche('mio', { owner: SID, ...PLANNED }))
    const { clock } = world(on, files)
    await start($)
    const ui = await $.ui.mount({ ...WORKING, surface: 'terminal' })
    files.set(`${DIR}/mio.json`, JSON.stringify(fiche('mio', { owner: SID, ...PLANNED, done: 4, status: 'done', started: ago(72), updated: ago(0) })))
    await clock.advance(5000)
    expect(await texts(ui)).toContain('Plan mio · terminado')
    expect(await texts(ui)).toContain('4 pasos en 1 h 12 min')
    await submit($)
    expect(await texts(ui)).toBe('engine')
  })

  test('con texto largo, el texto encoge y ▸ sigue a la vista para desplegar', IN_ES, async ($, on) => {
    const long = 'Restos de radar-portales y prompts de mantenimiento con un título que no cabe en ninguna banda '.repeat(3)
    world(on, filesOf(fiche('mio', { owner: SID, ...NEAR_END, title: long })))
    await start($)
    for (const surface of SURFACES) {
      const ui = await $.ui.mount({ ...WORKING, surface })
      const seen: boolean[] = []
      for (let i = 0; i < 2; i++) {
        const pinned = (await ui.find({ key: 'panel' })) !== undefined // fijar dura entre montajes
        seen.push(pinned)
        const boxes = await ui.findAll({ type: 'Box' })
        const growing = boxes.filter(b => b.props.flexGrow === 1)
        expect(growing.length).toBeGreaterThan(0)
        for (const b of growing) expect(b.props.minWidth).toBe(0)
        const fixed = boxes.filter(b => b.props.flexShrink === 0)
        expect(fixed.length).toBeGreaterThanOrEqual(pinned ? 2 : 1) // ▸; fijada, también «Panel» y las horas de la lista
        expect(await ui.find({ key: 'fijar' })).toBeDefined()
        await ui.press({ key: 'fijar' })
      }
      expect(seen).toContain(true) // ▸ despliega y ▾ pliega
      expect(seen).toContain(false)
      await ui.unmount()
    }
  })

  test('sin ficha deja la banda al motor', IN_ES, async ($, on) => {
    world(on, filesOf(fiche('ajena', { cwd: '/otra' })))
    await start($)
    expect(await texts(await $.ui.mount({ ...BAND, surface: 'terminal' }))).toBe('engine')
  })
})

describe('panel', () => {
  test('filas cerradas: paso actual y N/M; el estado solo si es una excepción', IN_ES, async ($, on) => {
    world(on, filesOf(fiche('mio', { owner: SID, ...PLANNED }), fiche('pausa', { status: 'paused', updated: ago(60 * 30) })))
    await start($)
    for (const surface of [...SURFACES, 'mobile'] as const) {
      const ui = await $.ui.mount({ ...PANE, surface })
      const all = await texts(ui)
      expect(all).toContain('Escribir el mod')
      expect(all).toContain('1/4')
      expect(all).not.toContain('En marcha')
      expect(all).toContain('En pausa')
      await ui.unmount()
    }
  })

  test('0.4.1: esta sesión primero, sin «Otra sesión», nombre del plan y tokens', IN_ES, async ($, on) => {
    const mio = {
      ...PLANNED,
      owner: SID,
      steps: [{ key: 'api', label: 'nota al cerrar', at: ago(5), tok: 12_400 }],
      tokens: { input: 1000, cache_write: 0, cache_read: 1_200_000, output: 3000 },
    }
    world(on, filesOf(fiche('ajena', { owner: 'otra' }), fiche('mio', mio), fiche('fin', { status: 'done', done: 10, tokens: { output: 900 } })))
    await start($)
    const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
    await ui.press({ key: 'abrir-mio' })
    const all = await texts(ui)
    expect(all.indexOf('Plan mio')).toBeLessThan(all.indexOf('Plan ajena'))
    expect(all).not.toContain('Otra sesión')
    expect(all.split('\n')).not.toContain('en curso')
    expect(all).toContain('Leer la API')
    expect(all).not.toContain('nota al cerrar')
    expect(all).toContain('12 k tokens · hace 5 min')
    expect(all).toContain('900 tokens')
    expect(all).not.toContain('10/10')
  })

  test('al abrir una fila: una sola lista y el subtítulo desaparece', IN_ES, async ($, on) => {
    world(on, filesOf(fiche('mio', { owner: SID, ...PLANNED })))
    await start($)
    const ui = await $.ui.mount({ ...PANE, surface: 'mobile' })
    await ui.press({ key: 'abrir-mio' })
    const all = await texts(ui)
    expect(all.split('\n').filter(l => l === 'Escribir el mod')).toHaveLength(1)
    expect(all).toContain('Leer la API')
    expect(all).toContain('hace 5 min')
  })

  test('con 40 pasos, ventana de cinco; lo oculto se abre en su sitio', IN_ES, async ($, on) => {
    world(on, filesOf(fiche('lotes', { owner: SID, ...LOTES })))
    await start($)
    const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
    await ui.press({ key: 'abrir-lotes' })
    expect((await ui.find({ key: 'antes-lotes' }))?.props.label).toBe('▸ 20 pasos antes')
    expect((await ui.find({ key: 'despues-lotes' }))?.props.label).toBe('▸ y 15 más')
    expect(await texts(ui)).not.toContain('Lote 1\n')
    await ui.press({ key: 'despues-lotes' })
    expect(await texts(ui)).toContain('Lote 40')
    expect(await ui.find({ key: 'plegar-lotes' })).toBeDefined()
  })

  test('ocultar y vaciar llaman al CLI; el plan de este chat no se oculta', IN_ES, async ($, on) => {
    const { runs } = world(on, filesOf(fiche('mio', { owner: SID, ...PLANNED }), fiche('ajena', { owner: 'otra' }), fiche('fin', { status: 'done', done: 10 })))
    await start($)
    const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
    expect(await ui.find({ key: 'ocultar-mio' })).toBeUndefined()
    await ui.press({ key: 'ocultar-ajena' })
    expect(cliArgs(runs[0])).toEqual(['hide', 'ajena'])
    await ui.press({ key: 'vaciar' })
    expect(cliArgs(runs[1])).toEqual(['clear'])
  })

  test('con solo cerradas de este chat, «Vaciar» va junto a «En este chat»', IN_ES, async ($, on) => {
    const { runs } = world(on, filesOf(fiche('mio-fin', { owner: SID, status: 'done', done: 10 })))
    await start($)
    const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
    expect(await texts(ui)).not.toContain('TERMINADAS HOY')
    await ui.press({ key: 'vaciar' })
    expect(cliArgs(runs[0])).toEqual(['clear'])
  })

  test('la banda enseña la ficha sin dueño de la carpeta, nunca la de otro chat', IN_ES, async ($, on) => {
    world(on, filesOf(fiche('ajena', { owner: 'otra', title: 'De otro chat' }), fiche('suelta', { owner: null, title: 'Sin dueño' })))
    await start($)
    const all = await texts(await $.ui.mount({ ...BAND, surface: 'terminal' }))
    expect(all).toContain('Sin dueño')
    expect(all).not.toContain('De otro chat')
  })

  test('«Ir al chat»: lo resuelve el CLI; si devuelve un resume, se copia; nunca en este chat', IN_ES, async ($, on) => {
    const desk = 'local_00000000-0000-4000-8000-000000000001'
    const cli = '460832df-56c9-4b74-aa1b-e720a7e1bc62'
    const { runs, copied } = world(on, filesOf(
      fiche('mio', { owner: SID, ...PLANNED }),
      fiche('app', { owner: 'otra', chat: { app: 'desktop', id: desk } }),
      fiche('term', { owner: cli, status: 'done', done: 10 }),
    ))
    await start($)
    const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
    expect(await ui.find({ key: 'chat-mio' })).toBeUndefined()
    await ui.press({ key: 'chat-app' })
    expect(cliArgs(runs[0])).toEqual(['chat', 'app'])
    expect(copied).toEqual([])
    await ui.press({ key: 'chat-term' })
    expect(copied[0]).toBe(`claude --resume ${cli}`)
  })

  test('secciones: este chat, otros por quién tiene que moverse, terminadas de hoy', IN_ES, async ($, on) => {
    const files = filesOf(
      fiche('mio', { owner: SID, ...PLANNED }),
      fiche('mio-fin', { owner: SID, status: 'done', done: 10, title: 'Cerrado aquí' }),
      fiche('pausa', { owner: 'p', status: 'paused' }),
      fiche('parada', { owner: 'q', updated: ago(45) }),
      fiche('trabaja', { owner: 'w', updated: ago(40) }),
      fiche('espera', { owner: 'e', updated: ago(2) }),
      fiche('oculta', { owner: 'h', hidden: true }),
      fiche('cancelada', { owner: 'c', status: 'cancelled', done: 2, total: 3, updated: ago(10) }),
      fiche('ayer', { owner: 'y', status: 'done', updated: ago(60 * 20) }),
    )
    files.set(`${DIR}/_turns/w.json`, JSON.stringify({ turn_start: NOW / 1000 - 600 }))
    files.set(`${DIR}/_turns/q.json`, JSON.stringify({ turn_start: NOW / 1000 - 3600, turn_end: NOW / 1000 - 3000 }))
    world(on, files)
    await start($)
    const all = await texts(await $.ui.mount({ ...PANE, surface: 'mobile' }))
    const order = ['EN ESTE CHAT', 'Plan mio', 'Cerrado aquí', 'OTROS PLANES EN CURSO', 'Plan espera', 'Plan trabaja', 'Plan parada', 'Plan pausa', 'TERMINADAS HOY EN OTROS CHATS', 'Plan cancelada']
    expect(order.map(t => all.indexOf(t))).toEqual([...order.map(t => all.indexOf(t))].sort((a, b) => a - b))
    expect(order.every(t => all.includes(t))).toBe(true)
    expect(all).toContain('esperando tu respuesta')
    expect(all).toContain('cancelada con 2/3')
    expect(all).not.toContain('Plan oculta')
    expect(all).not.toContain('Plan ayer') // terminada antes de las 00:00
    expect(all.split('Cerrado aquí').length).toBe(2) // lo de este chat sale una vez
  })
})

describe('hitos en la conversación', () => {
  test('el último paso cerrado dice «Ahora» y lo cerrado debajo', IN_ES, async ($, on) => {
    world(on, filesOf(fiche('mio', { owner: SID, ...PLANNED })))
    await start($)
    for (const surface of [...SURFACES, 'mobile'] as const) {
      const ui = await $.ui.mount({ ...toolUse('~/.claude/bin/progress step mio "Leer la API" --key api', 'mio · 1/4 · 25%\n'), surface })
      const all = await texts(ui)
      expect(all).toContain('Ahora: Escribir el mod')
      expect(all).toContain('✓ Leer la API · 1/4')
      await ui.unmount()
    }
  })

  test('un Bash cualquiera y su resultado quedan como los dibuja el motor', IN_ES, async ($, on) => {
    world(on, filesOf(fiche('mio', { owner: SID, ...PLANNED })))
    await start($)
    expect(await texts(await $.ui.mount({ ...toolUse('ls -la', 'total 0'), surface: 'terminal' }))).toBe('engine')
    const result = (stdout: string) =>
      ({ plugin: 'progress-ring', component: 'ToolResult', props: { tool_use_id: 't1', tool: 'Bash', output: out(stdout), isErrored: false } }) as const
    expect(await texts(await $.ui.mount({ ...result('mio · 1/4 · 25%\n'), surface: 'terminal' }))).toBe('')
    expect(await texts(await $.ui.mount({ ...result('otra cosa'), surface: 'terminal' }))).toBe('engine')
  })

  test('un grupo plegado con un hito lo enseña y se despliega al pedirlo', IN_ES, async ($, on) => {
    world(on, filesOf(fiche('mio', { owner: SID, ...PLANNED })))
    await start($)
    const call = (id: string, tool: string, input: unknown, stdout: string) => ({ tool_use_id: id, tool, input, isRunning: false, isErrored: false, isInterrupted: false, output: out(stdout) })
    const ui = await $.ui.mount({
      plugin: 'progress-ring',
      surface: 'desktop',
      component: 'ToolGroup',
      props: {
        calls: [call('g1', 'Read', { file_path: '/x' }, ''), call('g2', 'Bash', { command: 'progress step mio "Leer la API" --key api' }, 'mio · 1/4 · 25%')],
        isActive: false,
        isExpanded: false,
      },
    })
    expect(await texts(ui)).toContain('Ahora: Escribir el mod')
    expect((await ui.find({ key: 'grupo-g1-ver' }))?.props.label).toBe('▸ ver los 2 comandos')
    await ui.press({ key: 'grupo-g1-ver' })
    expect(await texts(ui)).toBe('grupo desplegado: true')
  })
})

describe('lista de tareas de la app', () => {
  const PLAN = 'mcp__ccd_turn__update_plan'
  const ruleIn = async ($: Engine, tools: string[]) =>
    (await $.prompt.compose({ model: 'm', promptModel: 'm', surfaces: [], tools, outputStyle: null, traits: [] })).sections.find(s => s.id === 'progress-rule')?.text ?? ''
  const hookRuns = (runs: string[][]) => runs.filter(r => r[1] === 'bin/progress_hook.py').length

  test('con update_plan entre las herramientas, la regla deja el plan a la lista', async ($, on) => {
    world(on, new Map())
    on('prompt.compose', () => ({ sections: [] }))
    await start($)
    const app = await ruleIn($, ['Bash', PLAN, 'mcp__ccd_turn__wrap_up'])
    expect(app).toContain('your task list already drives the card')
    expect(app).toContain('child accept <id> <cid>` (no extra step: your task list already counts that work)')
    expect(app).not.toContain('Close each step')
    expect(app).toContain('One card is one objective')
    expect(app).toContain('without the previous objective\'s finished steps')
    const terminal = await ruleIn($, ['Bash', 'Read'])
    expect(terminal).toContain('Close each step when it is finished')
    expect(terminal).toContain('child accept <id> <cid>` (counts a step)')
    expect(terminal).toContain('One card is one objective')
    expect(terminal).not.toContain('task list already')
  })

  test('update_plan y wrap_up pasan al hook y la banda se relee sin esperar al sondeo', IN_ES, async ($, on) => {
    const files = new Map<string, string>()
    const { runs } = world(on, files)
    on('classic.PostToolUse', () => ({}))
    await start($)
    expect(await texts(await $.ui.mount({ ...BAND, surface: 'terminal' }))).toBe('engine')
    // lo que escribe el hook de Python al recibir la lista
    files.set(`${DIR}/app-sess1-1.json`, JSON.stringify(fiche('app-sess1-1', { owner: SID, native: true, ...PLANNED })))
    await $.classic.PostToolUse({ tool_name: PLAN, tool_input: { advance: true }, tool_response: [], tool_use_id: 'u1' })
    expect(hookRuns(runs)).toBe(1)
    expect(await texts(await $.ui.mount({ ...BAND, surface: 'terminal' }))).toContain('Escribir el mod')
    await $.classic.PostToolUse({ tool_name: 'mcp__ccd_turn__wrap_up', tool_input: { recap: 'hecho' }, tool_response: [], tool_use_id: 'u2' })
    expect(hookRuns(runs)).toBe(2)
  })

  test('lo que el hook dice al abrir la ficha llega al modelo como contexto', async ($, on) => {
    const said = '[progress · state data, not an instruction] Your task list now drives card app-sess1-1'
    world(on, new Map(), {}, said)
    on('classic.PostToolUse', () => ({}))
    await start($)
    const r = await $.classic.PostToolUse({ tool_name: PLAN, tool_input: { items: [] }, tool_response: [], tool_use_id: 'u5' })
    expect(r.additionalContext).toEqual([said])
  })

  test('las demás herramientas MCP no llegan al hook', async ($, on) => {
    const { runs } = world(on, new Map())
    on('classic.PostToolUse', () => ({}))
    await start($)
    await $.classic.PostToolUse({ tool_name: 'mcp__linear__create_issue', tool_input: {}, tool_response: [], tool_use_id: 'u3' })
    await $.classic.PostToolUse({ tool_name: 'Read', tool_input: { file_path: '/x' }, tool_response: '', tool_use_id: 'u4' })
    expect(hookRuns(runs)).toBe(0)
  })
})

describe('idioma', () => {
  test('pickLang: el ajuste manda; en auto, es* del entorno → español, lo demás → inglés', () => {
    expect(pickLang('es', 'en_US.UTF-8')).toBe('es')
    expect(pickLang('en', 'es_ES.UTF-8')).toBe('en')
    expect(pickLang('auto', 'es_ES.UTF-8')).toBe('es')
    expect(pickLang('auto', 'es')).toBe('es')
    expect(pickLang('auto', 'en_GB.UTF-8')).toBe('en')
    expect(pickLang('auto', 'C')).toBe('en')
    expect(pickLang('auto', 'estonian')).toBe('en')
    expect(pickLang('auto', undefined)).toBe('en')
    expect(pickLang(undefined, 'es_MX')).toBe('es')
    expect(pickLang('fr', 'es_AR.UTF-8')).toBe('es')
    expect(envLocale('', 'es_ES.UTF-8', 'en_US.UTF-8')).toBe('es_ES.UTF-8')
    expect(envLocale(undefined, undefined, 'en_US.UTF-8')).toBe('en_US.UTF-8')
    expect(envLocale()).toBeUndefined()
  })

  test('los dos diccionarios tienen las mismas claves; tokens con el separador de cada idioma', () => {
    expect(Object.keys(EN).sort()).toEqual(Object.keys(ES).sort())
    expect([tokText(1_234_000, ES), tokText(12_400, ES), tokText(850, ES)]).toEqual(['1,2 M tokens', '12 k tokens', '850 tokens'])
    expect([tokText(1_234_000, EN), tokText(12_400, EN), tokText(850, EN)]).toEqual(['1.2M tokens', '12k tokens', '850 tokens'])
    expect(stepRows(fiche('p', PLANNED), EN).at(-1)?.label).toBe('2 unnamed pending steps')
  })

  test('banda en inglés', IN_EN, async ($, on) => {
    world(on, filesOf(fiche('mio', { owner: SID, ...PLANNED })))
    await start($)
    for (const surface of SURFACES) {
      const busy = await $.ui.mount({ ...WORKING, surface })
      expect(await texts(busy)).toContain('Plan mio · ✓ Leer la API · 5 min ago')
      await busy.unmount()
      const idle = await $.ui.mount({ ...BAND, surface })
      expect(await texts(idle)).toContain('waiting for your reply · now: Escribir el mod')
      await idle.unmount()
    }
  })

  test('panel en inglés', IN_EN, async ($, on) => {
    world(on, filesOf(
      fiche('mio', { owner: SID, ...PLANNED }),
      fiche('pausa', { owner: 'p', status: 'paused' }),
      fiche('fin', { owner: 'f', status: 'done', done: 10, tokens: { output: 900 } }),
    ))
    await start($)
    for (const [i, surface] of [...SURFACES, 'mobile'].entries()) {
      const ui = await $.ui.mount({ ...PANE, surface: surface as (typeof SURFACES)[number] | 'mobile' })
      if (i === 0) await ui.press({ key: 'abrir-mio' }) // la fila abierta sigue abierta en las demás superficies
      const all = await texts(ui)
      for (const t of ['IN THIS CHAT', 'OTHER PLANS IN PROGRESS', 'FINISHED TODAY IN OTHER CHATS', 'Paused', '2 unnamed pending steps', '5 min ago', '10 steps in 57 min · 900 tokens']) {
        expect(all).toContain(t)
      }
      expect(all).not.toContain('EN ESTE CHAT')
      expect((await ui.find({ key: 'ocultar-pausa' }))?.props.label).toBe('Hide')
      expect((await ui.find({ key: 'vaciar' }))?.props.label).toBe('Clear')
      expect((await ui.find({ key: 'discreto' }))?.props.label).toBe('Discreet mode: off')
      await ui.unmount()
    }
  })

  test('en auto, el locale del entorno decide', async ($, on) => {
    world(on, filesOf(fiche('mio', { owner: SID, ...PLANNED })), { LANG: 'es_ES.UTF-8' })
    await start($)
    expect(await texts(await $.ui.mount({ ...BAND, surface: 'terminal' }))).toContain('esperando tu respuesta')
  })

  test('en auto y sin locale, inglés', async ($, on) => {
    world(on, filesOf(fiche('mio', { owner: SID, ...PLANNED })))
    await start($)
    expect(await texts(await $.ui.mount({ ...BAND, surface: 'terminal' }))).toContain('waiting for your reply')
  })
})
