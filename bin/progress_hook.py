#!/usr/bin/env python3
"""Hook único para Claude Code. Uso: progress_hook.py <evento>, payload JSON por stdin.

Eventos: UserPromptSubmit · Stop · PreToolUse (Agent) · PostToolUse (Agent, Bash y la lista de
tareas de la app: update_plan y wrap_up) · SubagentStart · SubagentStop.

S4: nunca bloquea. Cualquier error → sale 0 sin salida. No usa `decision`.
"""
import json
import os
import re
import sys
import tempfile
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

TURN_MIN_S = 10 * 60  # turno largo sin tocar la ficha → aviso en el siguiente mensaje
CLI = os.path.join(os.path.dirname(os.path.realpath(__file__)), "progress")
CLI = '"%s"' % CLI if " " in CLI else CLI
MAX_KIDS = 5          # hijos listados en el contexto; el resto, como recuento


def turns_dir(P):
    P.ensure_dir()  # si el hook llega antes que el CLI, el directorio nace ya con 700
    d = os.path.join(P.PROGRESS_DIR, "_turns")
    os.makedirs(d, exist_ok=True)
    return d


def turn_file(P, sid):
    safe = "".join(c for c in (sid or "nosession") if c.isalnum() or c == "-")[:64]
    return os.path.join(turns_dir(P), safe + ".json")


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def write_json(path, data):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")  # 600, nombre aleatorio
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def q(P, text, n=80):
    """Texto de la ficha entre comillas JSON: no puede cerrarlas ni partir la línea."""
    return json.dumps(P.clean(text, n), ensure_ascii=False)


def fiche_for(P, payload, own_only=False):
    """La ficha de esta sesión (esté donde esté); si no tiene y own_only es False, la de la carpeta."""
    sid = payload.get("session_id")
    if sid:
        owned = [d for d in P.load_all()[0] if d.get("status") == "active" and d.get("owner") == sid]
        if owned:
            return max(owned, key=lambda d: P.parse_iso(d.get("updated")))
    if own_only:
        return None  # los hijos y el aviso de Stop nunca van a la ficha de otra sesión
    found = P.for_cwd(payload.get("cwd") or os.getcwd(), statuses=("active",))
    return found[0] if found else None


CLAIM_TAIL = (r"\s+(child\s+[a-z]+|start|step|set|plan|resume|pause|done|cancel)\s+"
              r"([a-z0-9][a-z0-9._-]{0,63})(?![a-z0-9._-])")
VAR_RE = re.compile(r"(\w+)=[\"']?[^\s;&|\"']*/progress[\"']?(?=[\s;&|]|$)")


def claimed(cmd):
    """(verbo, id) de cada `progress` del comando, llamado directo o vía variable (P=…/progress; $P step x)."""
    heads = [r"(?:^|[\s/;&|(])progress"]
    heads += [r"\$\{?%s\}?" % re.escape(v) for v in VAR_RE.findall(cmd)]
    rx = re.compile("(?:%s)%s" % ("|".join(heads), CLAIM_TAIL))
    return [(v.split()[0], i) for v, i in rx.findall(cmd)]


def claimed_ids(cmd):
    """Ids sobre los que actúa `progress`."""
    return list(dict.fromkeys(i for _, i in claimed(cmd)))


def chat_ref(P, sid):
    """Cómo volver al chat que acaba de tocar la ficha: en la app de escritorio, su id de la app
    (abre `claude://claude.ai/epitaxy/<id>`); si no, el de la sesión (`claude --resume <id>`)."""
    host = os.environ.get("CLAUDE_CODE_HOST_SESSION_ID") or ""
    if os.environ.get("CLAUDE_CODE_ENTRYPOINT") == "claude-desktop" and re.fullmatch(r"local_[0-9a-f-]{36}", host):
        return {"app": "desktop", "id": host, "at": P.iso(P.now())}
    return {"app": "cli", "id": sid, "at": P.iso(P.now())}


def on_post_bash(P, payload):
    """Quien usa `progress` sobre una ficha pasa a ser su sesión dueña, y la ficha apunta los tokens
    gastados desde la última vez (ver progresslib.mark_tokens)."""
    sid = payload.get("session_id")
    cmd = (payload.get("tool_input") or {}).get("command") or ""
    if not sid or "progress" not in cmd:
        return
    calls = claimed(cmd)
    cum = P.session_tokens(payload.get("transcript_path")) if calls else None
    for aid in claimed_ids(cmd):
        verb = [v for v, i in calls if i == aid][-1]
        try:
            with P.mutate(aid) as f:
                f["owner"] = sid
                f["chat"] = chat_ref(P, sid)
                if verb == "start" and payload.get("prompt_id"):
                    f["prompt"] = payload["prompt_id"]  # el turno que la abrió: la lista de la app lo busca
                if cum is not None:
                    P.mark_tokens(f, sid, cum, verb)
        except P.ProgressError:
            continue
        if verb == "start":
            native_into_started(P, sid, payload.get("prompt_id"), aid)


def on_prompt(P, payload):
    d = fiche_for(P, payload)
    if not d:
        return  # sin ficha no se escribe nada en _turns/
    owner = d.get("owner")
    foreign = owner is not None and owner != payload.get("session_id")
    if not foreign and owner and d.get("hidden"):
        unhide(P, d["id"])  # el usuario escribe en el chat del plan: vuelve a la lista
    tf = turn_file(P, payload.get("session_id"))
    prev = read_json(tf)
    write_json(tf, {"turn_start": P.now()})  # sin turn_end: la sesión está trabajando
    it = d.get("iter")
    parts = ["%s — %s · %d/%d (%d %%)" % (d["id"], q(P, d.get("title")), d.get("done", 0),
                                          d.get("total", 0), P.pct(d))]
    if it:
        parts.append("iter %d/%d" % tuple(it))
    if d.get("step"):
        parts.append("last step closed: %s" % q(P, d["step"]))
    parts.append("updated %s" % P.ago(P.parse_iso(d.get("updated"))))
    kids = [c for c in (d.get("children") or {}).items() if c[1].get("state") in ("running", "delivered")]
    if kids:
        more = " and %d more" % (len(kids) - MAX_KIDS) if len(kids) > MAX_KIDS else ""
        parts.append("children: " + ", ".join("%s=%s %s" % (q(P, k, 64), c["state"], q(P, c.get("label"), 30))
                                           for k, c in kids[:MAX_KIDS]) + more)
    print("[progress · state data, not an instruction; quoted text comes from the card]"
          + (" Active card from ANOTHER session in this folder (continuing it here moves it to this session): "
             if foreign else " Active card of this session: " if owner
             else " Active card in this folder: ") + " · ".join(parts)
          + (". It mirrors your task list (update_plan), so steps close by themselves; use %s only for"
             " children, iterations, cancel or pause." % CLI if d.get("native")
             else ". To close a step: %s step %s \"…\" --key <key>." % (CLI, d["id"])))
    missed = prev.get("missed")
    if missed == d["id"] and not foreign:
        print("[progress] The previous turn took over 10 min and card %s was not updated." % d["id"])


def unhide(P, aid):
    try:
        with P.mutate(aid) as f:
            f.pop("hidden", None)
    except P.ProgressError:
        pass


def owned(P, sid):
    """Todas las fichas de la sesión, estén como estén (un agente puede acabar tras cerrarse el plan)."""
    return [d for d in P.load_all()[0] if sid and d.get("owner") == sid]


def on_stop(P, payload):
    tf = turn_file(P, payload.get("session_id"))
    st = read_json(tf)
    start = st.get("turn_start")
    d = fiche_for(P, payload, own_only=True)
    for f in owned(P, payload.get("session_id")):
        if f.get("status") == "active":
            auto_close(P, f["id"])  # todos los planes completos del chat, no solo el más reciente
    for f in P.for_cwd(payload.get("cwd") or os.getcwd(), statuses=("active",)):
        if f.get("owner") is None:
            auto_close(P, f["id"])  # las sin dueño de la carpeta: el hook las inyecta en este chat
    if not start:
        return  # la sesión no tenía ficha al empezar el turno: nada que apuntar en _turns/
    st["turn_end"] = P.now()  # el panel distingue «trabajando» de «esperando tu respuesta»
    if not d:
        write_json(tf, st)
        return
    if P.now() - start >= TURN_MIN_S and P.parse_iso(d.get("updated")) < start:
        st["missed"] = d["id"]
    else:
        st.pop("missed", None)
    write_json(tf, st)


def auto_close(P, aid):
    """Fin de turno con todos los pasos cerrados y ningún agente en marcha: el plan se cierra solo."""
    ready = lambda f: (f.get("status") == "active" and (f.get("total") or 0) > 0
                       and (f.get("done") or 0) >= f["total"] and not P.running_children(f))
    try:
        if not ready(P.read(aid)):
            return  # escribir sin cambiar nada refrescaría `updated` y escondería una parada
        with P.mutate(aid) as f:
            if ready(f):
                f["status"] = "done"
                f.pop("hidden", None)
    except (P.ProgressError, OSError, ValueError):
        pass


def child_id(payload):
    tid = payload.get("tool_use_id") or ""
    return ("a-" + "".join(c for c in tid if c.isalnum())[-24:]) if tid else None


def on_pre_agent(P, payload):
    d = fiche_for(P, payload, own_only=True)
    cid = child_id(payload)
    if not d or not cid:
        return
    ti = payload.get("tool_input") or {}
    label = ti.get("description") or ti.get("subagent_type") or "agent"
    with P.mutate(d["id"]) as f:
        t = P.iso(P.now())
        f.pop("hidden", None)  # un agente nuevo es actividad: vuelve a la lista
        f.setdefault("children", {})[cid] = {
            "label": P.clean(label), "state": "running", "started": t, "updated": t,
            "type": P.clean(ti.get("subagent_type") or "", 40),
            "background": bool(ti.get("run_in_background", True)),
        }


def set_child(P, payload, match, state):
    for d in owned(P, payload.get("session_id")):
        if not any(match(k, c) and c.get("state") == "running" for k, c in (d.get("children") or {}).items()):
            continue
        with P.mutate(d["id"]) as f:
            for cid, c in (f.get("children") or {}).items():
                if match(cid, c) and c.get("state") == "running":
                    c["state"] = state
                    c["updated"] = P.iso(P.now())
                    return


def on_post_tool(P, payload):
    if payload.get("tool_name") in (NATIVE_PLAN, NATIVE_WRAP):
        on_native_plan(P, payload)
    elif payload.get("tool_name") == "Bash":
        on_post_bash(P, payload)
    else:
        on_post_agent(P, payload)


def on_post_agent(P, payload):
    # En primer plano, el retorno del tool = el agente ha terminado.
    cid = child_id(payload)
    set_child(P, payload, lambda k, c: k == cid and not c.get("background"), "delivered")


def on_subagent_start(P, payload):
    aid, typ = payload.get("agent_id"), payload.get("agent_type") or ""
    d = fiche_for(P, payload, own_only=True)
    if not d or not aid:
        return
    with P.mutate(d["id"]) as f:
        cands = [c for c in (f.get("children") or {}).values()
                 if c.get("state") == "running" and not c.get("agent_id")
                 and (not typ or not c.get("type") or c.get("type") == typ)]
        if cands:
            max(cands, key=lambda c: P.parse_iso(c.get("started")))["agent_id"] = aid


def on_subagent_stop(P, payload):
    aid = payload.get("agent_id")
    if aid:
        set_child(P, payload, lambda k, c: c.get("agent_id") == aid, "delivered")


# ── Lista de tareas de la app de escritorio ────────────────────────────────────────────────────────
# En la app, el modelo lleva su lista con update_plan y la cierra con wrap_up. El hook la refleja en una
# ficha propia de la sesión (`app-<sesión>-<n>`, con `native: true`), así el modelo no lleva dos listas.
# Lo que la lista no tiene (iteraciones, subagentes, cancel, pause) sigue yendo por el CLI.
# La última lista de cada sesión se guarda en _native/<sesión>.json: advance, done y start son cambios
# sobre ella, y sus claves de paso sobreviven a los replanes.

NATIVE_PLAN = "mcp__ccd_turn__update_plan"
NATIVE_WRAP = "mcp__ccd_turn__wrap_up"
NATIVE_MIN = 3  # el umbral de la regla: solo los planes de 3 pasos o más abren ficha
NATIVE_STATES = ("pending", "in_progress", "completed")


def native_file(P, sid, create=True):
    d = os.path.join(P.PROGRESS_DIR, "_native")
    if create:
        P.ensure_dir()
        os.makedirs(d, exist_ok=True)
    safe = "".join(c for c in sid if c.isalnum() or c == "-")[:64]
    return os.path.join(d, safe + ".json")


def native_items(P, raw):
    """La lista que manda la app → [{content, status}]; None si no tiene la forma esperada."""
    if not isinstance(raw, list) or not all(isinstance(i, dict) for i in raw):
        return None
    out = []
    for i in raw:
        content = P.clean(i.get("content"))
        if content:
            out.append({"content": content, "status": i["status"] if i.get("status") in NATIVE_STATES else "pending"})
    return out


def native_step(P, items, ti):
    """advance, done y start sobre la lista anterior, con la semántica de la app."""
    items = [dict(i) for i in items]
    if ti.get("advance") is True:
        cur = next((i for i in items if i["status"] == "in_progress"), None)
        if cur:
            cur["status"] = "completed"
        nxt = next((i for i in items if i["status"] == "pending"), None)
        if nxt:
            nxt["status"] = "in_progress"
        return items
    done, start = ti.get("done"), ti.get("start")
    if isinstance(done, list) or type(start) is int:
        ok = lambda n: type(n) is int and 1 <= n <= len(items)
        for n in done or []:
            if ok(n):
                items[n - 1]["status"] = "completed"
        if ok(start):
            items[start - 1]["status"] = "in_progress"
        return items
    raise P.ProgressError("update_plan without a known shape: %s" % sorted(ti))


def slug(text):
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:40].strip("-") or "step"


def native_keys(new, old, closed):
    """Clave de cada paso: la del paso anterior con el mismo texto; si no, la del anterior en la misma
    posición, si nadie la ha tomado y no está cerrada (un paso renombrado); si no, una nueva. Una clave
    nueva nunca repite una vieja: un paso quitado y vuelto a poner con otro texto no hereda su cierre."""
    keys, taken = [None] * len(new), set()
    for i, n in enumerate(new):
        k = next((o["key"] for o in old if o["content"] == n["content"] and o["key"] not in taken), None)
        if k:
            keys[i] = k
            taken.add(k)
    for i in range(len(new)):
        if keys[i] is None and i < len(old) and old[i]["key"] not in taken and old[i]["key"] not in closed:
            keys[i] = old[i]["key"]
            taken.add(keys[i])
    used = taken | set(closed) | {o["key"] for o in old}
    for i, n in enumerate(new):
        if keys[i] is None:
            base = k = slug(n["content"])
            j = 2
            while k in used:
                k, j = "%s-%d" % (base, j), j + 1
            keys[i] = k
            used.add(k)
    return [dict(n, key=k) for n, k in zip(new, keys)]


def native_id(P, sid, st):
    """`app-<8 primeros de la sesión>-<n>`: ni el de una ficha viva ni el de una archivada."""
    base = "app-" + "".join(c for c in sid.lower() if c.isalnum())[:8]
    ddir = os.path.join(P.PROGRESS_DIR, P.DONE_DIR_NAME)
    archived = os.listdir(ddir) if os.path.isdir(ddir) else []
    n = int(st.get("n") or 0)
    while True:
        n += 1
        aid = "%s-%d" % (base, n)
        if not os.path.exists(P.path_for(aid)) and not any(
                re.fullmatch(re.escape(aid) + r"-\d{8}-\d{6}\.json", a) for a in archived):
            st["n"] = n
            return aid


def new_objective(new, old, closed):
    """Una ficha es un objetivo, no la sesión: una lista que no repite ningún paso de la anterior,
    cuando ya hay trabajo hecho, es otro objetivo. Sin nada hecho es solo el mismo plan reescrito."""
    if not closed:
        return False
    before = {o["content"] for o in old}
    return not any(n["content"] in before for n in new)


def closed_keys(P, aid):
    try:
        return set(P.read(aid).get("keys") or []) if aid else set()
    except (P.ProgressError, OSError, ValueError):
        return set()


def retire(P, aid):
    """Una lista nueva (`new_task`) es otra tarea: la ficha anterior se da por terminada si lo estaba
    y, si no, por cancelada. Vuelve sola si alguien la retoma con el CLI."""
    try:
        if P.read(aid).get("status") != "active":
            return
        with P.mutate(aid) as f:
            if f.get("status") == "active":
                full = (f.get("done") or 0) >= (f.get("total") or 0) > 0
                f["status"] = "done" if full else "cancelled"
    except (P.ProgressError, OSError, ValueError):
        pass


def native_sync(P, f, items, sid, st, cum, verb):
    """La ficha pasa a tener el plan de la lista y los pasos que la lista da por hechos."""
    P.set_plan(f, ["%s=%s" % (i["key"], i["content"]) for i in items])
    for i in items:
        if i["status"] == "completed":
            P.close_step(f, label=i["content"], key=i["key"])
    f["native"] = True
    f["owner"] = sid
    f["chat"] = chat_ref(P, sid)
    if cum is not None:
        P.mark_tokens(f, sid, cum, verb)


def started_this_turn(P, sid, pid):
    """La ficha que el modelo abrió con `progress start` en este mismo turno, si la hay."""
    if not pid:
        return None
    found = [d for d in P.load_all()[0] if d.get("owner") == sid and d.get("status") == "active"
             and not d.get("native") and d.get("prompt") == pid]
    return max(found, key=lambda d: P.parse_iso(d.get("updated")))["id"] if found else None


def native_into_started(P, sid, pid, aid):
    """`progress start` llega después de la lista, en el mismo turno: la lista pasa a esa ficha (con sus
    subagentes) y la que abrió el hook se archiva en _done/, sin borrarse. Así no quedan dos fichas."""
    nf = native_file(P, sid, create=False)
    st = read_json(nf)
    prev_id = st.get("card")
    if not pid or st.get("prompt") != pid or not prev_id or prev_id == aid or not st.get("items"):
        return
    try:
        prev = P.read(prev_id)
    except (P.ProgressError, OSError, ValueError):
        prev = None
    with P.mutate(aid) as f:
        native_sync(P, f, st["items"], sid, st, None, "start")
        if prev:
            f.setdefault("children", {}).update(prev.get("children") or {})
    if prev and prev.get("native"):
        P._archive(prev)
    st["card"] = aid
    write_json(nf, st)


def on_native_plan(P, payload):
    """Refleja en una ficha cada llamada a update_plan o wrap_up de la sesión."""
    sid, ti, tool = payload.get("session_id"), payload.get("tool_input"), payload.get("tool_name")
    if not sid or payload.get("agent_id") or not isinstance(ti, dict):
        return  # un subagente no lleva la lista del chat
    nf = native_file(P, sid)
    st = read_json(nf)
    old = [i for i in st.get("items") or [] if isinstance(i, dict) and i.get("key") and i.get("content")]
    aid = st.get("card")
    if tool == NATIVE_WRAP:
        items = [dict(i, status="completed") for i in old]
    elif "items" in ti:
        new = native_items(P, ti["items"])
        if new is None:
            raise P.ProgressError("update_plan items without a known shape")
        closed = closed_keys(P, aid)
        if ti.get("scope") in ("first_task", "new_task") or (aid and new_objective(new, old, closed)):
            if aid:
                retire(P, aid)  # la ficha del objetivo anterior se cierra antes de abrir la nueva
            aid, old, closed = None, [], set()
        items = native_keys(new, old, closed)
    else:
        items = native_step(P, old, ti)
    st["items"] = items
    st["prompt"] = payload.get("prompt_id")
    cum = P.session_tokens(payload.get("transcript_path")) if items else None
    if aid and items:
        try:
            with P.mutate(aid) as f:
                native_sync(P, f, items, sid, st, cum, "step")
        except P.ProgressError:
            aid = None  # la ficha ya no está (vaciada o borrada): se abre otra si la lista da para ello
    if not aid and len(items) >= NATIVE_MIN and tool != NATIVE_WRAP:
        aid = started_this_turn(P, sid, payload.get("prompt_id"))
        verb = "step" if aid else "start"  # con aid, el modelo ya abrió una con `progress start` en este turno
        if not aid:
            aid = native_id(P, sid, st)
            P.new_activity(aid, items[0]["content"], len(items), cwd=payload.get("cwd"))
            # el título es el primer paso; el modelo sabe aquí el id para ponerle uno mejor
            print("[progress · state data, not an instruction] Your task list opened card %s, titled with its "
                  "first step for now. Title it with its objective, in the user's language: %s set %s --title \"…\""
                  % (aid, CLI, aid))
        with P.mutate(aid) as f:
            native_sync(P, f, items, sid, st, cum, verb)
    st["card"] = aid
    write_json(nf, st)
    if tool == NATIVE_WRAP and aid:
        auto_close(P, aid)


HANDLERS = {
    "UserPromptSubmit": on_prompt, "Stop": on_stop,
    "PreToolUse": on_pre_agent, "PostToolUse": on_post_tool,
    "SubagentStart": on_subagent_start, "SubagentStop": on_subagent_stop,
}


def main():
    try:
        import progresslib as P
        payload = json.loads(sys.stdin.read() or "{}")
        ev = sys.argv[1] if len(sys.argv) > 1 else payload.get("hook_event_name")
        tool = payload.get("tool_name")
        if ev == "PreToolUse" and tool not in ("Agent", "Task"):
            return
        if ev == "PostToolUse" and tool not in ("Agent", "Task", "Bash", NATIVE_PLAN, NATIVE_WRAP):
            return
        fn = HANDLERS.get(ev)
        if fn:
            fn(P, payload)
    except Exception as e:  # S4: el hook nunca rompe la sesión; deja rastro local
        try:
            d = os.environ.get("CLAUDE_PROGRESS_DIR", os.path.expanduser("~/.claude/progress"))
            with open(os.path.join(d, "_hook-errors.log"), "a", encoding="utf-8") as f:
                f.write("%s %r\n" % (sys.argv[1:], e))
        except Exception:
            pass


if __name__ == "__main__":
    main()
    sys.exit(0)
