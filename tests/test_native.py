"""La lista de tareas de la app (update_plan y wrap_up) reflejada en una ficha.

native_f0.jsonl son los PostToolUse reales de la prueba F0 (2026-10-15): una sesión de la app con una
tarea de 4 pasos, tres `advance` y `wrap_up`. El resto de casos usan payloads con esa misma forma.
"""
import json
import os
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "bin", "progress_hook.py")
CLI = os.path.join(ROOT, "bin", "progress")
F0 = os.path.join(ROOT, "tests", "fixtures", "native_f0.jsonl")
PLAN, WRAP = "mcp__ccd_turn__update_plan", "mcp__ccd_turn__wrap_up"
SID = "5e55a0b1-0000-4000-8000-000000000001"
LOCAL = "local_11111111-2222-4333-8444-555555555555"


def items(*steps):
    """("a", "c") → paso a completado; ("b", "i") → en curso; texto solo → pendiente."""
    st = {"c": "completed", "i": "in_progress"}
    return [{"content": s, "status": "pending"} if isinstance(s, str) else {"content": s[0], "status": st[s[1]]}
            for s in steps]


class Native(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="vault con espacio @ "))
        self.dir = os.path.join(self.tmp, ".progress")
        self.cwd = os.path.join(self.tmp, "proyecto")
        os.makedirs(self.cwd)
        self.env = dict(os.environ, CLAUDE_PROGRESS_DIR=self.dir)
        for k in ("CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_HOST_SESSION_ID"):
            self.env.pop(k, None)  # el chat de la app en la que corren los tests no entra en las fichas

    def hook(self, ev, payload, env=None):
        base = {"session_id": SID, "cwd": self.cwd, "hook_event_name": ev}
        base.update(payload)
        return subprocess.run([HOOK, ev], input=json.dumps(base), env=env or self.env,
                              capture_output=True, text=True, timeout=5)

    def call(self, tool_input, tool=PLAN, **extra):
        """Lo que el hook le dice al modelo: solo habla cuando abre una ficha."""
        r = self.hook("PostToolUse", dict({"tool_name": tool, "tool_input": tool_input}, **extra))
        self.assertEqual(r.returncode, 0)
        return r.stdout

    def cli(self, *a):
        subprocess.run([CLI, *a], env=self.env, check=True, capture_output=True)

    def cards(self):
        names = os.listdir(self.dir) if os.path.isdir(self.dir) else []
        return sorted(n[:-5] for n in names if n.endswith(".json") and not n.startswith(("_", ".")))

    def card(self, aid):
        with open(os.path.join(self.dir, aid + ".json")) as f:
            return json.load(f)

    def plan_of(self, d):
        return [p["label"] for p in d.get("plan") or []]

    def test_f0_replay(self):
        with open(F0) as f:
            calls = [json.loads(line) for line in f]
        aid = "app-dfcbaf7b-1"
        seen = []
        said = []
        for c in calls:
            c = dict(c, cwd=self.cwd)  # la carpeta de la prueba no existe aquí
            r = self.hook("PostToolUse", c)
            self.assertEqual(r.returncode, 0)
            said.append(r.stdout)
            d = self.card(aid)
            seen.append((d["done"], d["total"], d["status"]))
        self.assertEqual(seen, [(0, 4, "active"), (1, 4, "active"), (2, 4, "active"), (3, 4, "active"),
                                (4, 4, "done")])
        d = self.card(aid)
        self.assertEqual(d["title"], "Crear a.txt con «uno»")
        self.assertEqual(self.plan_of(d), ["Crear a.txt con «uno»", "Crear b.txt con «dos»",
                                           "Crear c.txt con «tres»", "Listar contenido del scratchpad"])
        self.assertTrue(d["native"])
        self.assertEqual(d["owner"], "dfcbaf7b-f21b-41f9-bd87-1d0c7b5f9a13")
        self.assertEqual([s["label"] for s in d["steps"]], self.plan_of(d))
        self.assertEqual(self.cards(), [aid])
        self.assertIn("card %s, titled with its first step" % aid, said[0])
        self.assertIn("set %s --title" % aid, said[0])
        self.assertEqual(said[1:], [""] * 4)

    def test_short_list_opens_no_card_until_it_grows(self):
        self.call({"items": items(("A", "i"), "B"), "scope": "first_task"})
        self.assertEqual(self.cards(), [])
        self.call({"advance": True})
        self.call({"items": items(("A", "c"), ("B", "i"), "C"), "scope": "continuation"})
        aid = "app-5e55a0b1-1"
        self.assertEqual(self.cards(), [aid])
        d = self.card(aid)
        self.assertEqual((d["done"], d["total"]), (1, 3))
        self.assertEqual(d["keys"], ["a"])

    def test_done_and_start_by_number(self):
        self.call({"items": items(("A", "i"), "B", "C", "D"), "scope": "first_task"})
        self.call({"done": [1, 2], "start": 4})
        self.call({"done": [9, "x", True]})  # fuera de rango o sin ser un número: se ignora
        d = self.card("app-5e55a0b1-1")
        self.assertEqual((d["done"], d["total"]), (2, 4))
        self.assertEqual(d["keys"], ["a", "b"])

    def test_replan_keeps_closed_steps_and_keys(self):
        self.call({"items": items(("Leer el código", "i"), "Escribir tests", "Publicar"), "scope": "first_task"})
        self.call({"advance": True})
        # orden nuevo, un paso renombrado en su sitio, uno quitado y uno nuevo al final
        self.call({"items": items(("Leer el código", "c"), ("Escribir los tests", "i"), "Revisar", "Documentar"),
                   "scope": "continuation"})
        d = self.card("app-5e55a0b1-1")
        self.assertEqual(self.plan_of(d), ["Leer el código", "Escribir los tests", "Revisar", "Documentar"])
        self.assertEqual([p["key"] for p in d["plan"]], ["leer-el-codigo", "escribir-tests", "publicar", "documentar"])
        self.assertEqual((d["done"], d["total"]), (1, 4))

    def test_removed_closed_step_is_not_inherited(self):
        self.call({"items": items(("Paso uno", "c"), ("Paso dos", "i"), "Paso tres"), "scope": "first_task"})
        self.call({"items": items(("Otra cosa", "i"), "Paso dos", "Paso tres"), "scope": "continuation"})
        d = self.card("app-5e55a0b1-1")
        self.assertEqual([p["key"] for p in d["plan"]], ["otra-cosa", "paso-dos", "paso-tres"])
        self.assertEqual((d["done"], d["total"]), (1, 4))  # lo cerrado fuera del plan sigue contando

    def test_repeated_call_counts_once(self):
        call = {"items": items(("A", "c"), ("B", "i"), "C"), "scope": "first_task"}
        self.call(call)
        self.call(dict(call, scope="continuation"))
        self.assertEqual(self.card("app-5e55a0b1-1")["done"], 1)

    def test_new_task_retires_previous_card(self):
        self.call({"items": items(("A", "c"), ("B", "i"), "C"), "scope": "first_task"})
        self.call({"items": items(("X", "i"), "Y", "Z"), "scope": "new_task"})
        self.assertEqual(self.card("app-5e55a0b1-1")["status"], "cancelled")
        d = self.card("app-5e55a0b1-2")
        self.assertEqual((d["status"], d["done"], self.plan_of(d)), ("active", 0, ["X", "Y", "Z"]))

    def test_new_task_after_wrap_up_keeps_first_done(self):
        self.call({"items": items(("A", "i"), "B", "C"), "scope": "first_task"})
        self.call({"recap": "hecho"}, tool=WRAP)
        self.assertEqual(self.card("app-5e55a0b1-1")["status"], "done")
        self.call({"items": items(("X", "i"), "Y", "Z"), "scope": "new_task"})
        self.assertEqual(self.card("app-5e55a0b1-1")["status"], "done")
        self.assertEqual(self.card("app-5e55a0b1-2")["status"], "active")

    def test_continuation_reopens_wrapped_card(self):
        self.call({"items": items(("A", "i"), "B", "C"), "scope": "first_task"})
        self.call({"recap": "hecho"}, tool=WRAP)
        self.call({"items": items(("A", "c"), ("B", "c"), ("C", "c"), ("D", "i")), "scope": "continuation"})
        d = self.card("app-5e55a0b1-1")
        self.assertEqual((d["status"], d["done"], d["total"]), ("active", 3, 4))
        self.assertEqual(self.cards(), ["app-5e55a0b1-1"])

    # ── Una ficha por objetivo (requerimiento del 2026-10-19: app-75aa0a2b-1 sumó 55 pasos de varios) ──

    def test_two_objectives_in_a_row_leave_two_cards(self):
        self.call({"items": items(("Artefacto A", "i"), "Artefacto B", "Artefacto C"), "scope": "first_task"})
        self.call({"advance": True})
        self.call({"advance": True})
        self.call({"recap": "hecho"}, tool=WRAP)
        said = self.call({"items": items(("Deck A", "i"), "Deck B", "Deck C"), "scope": "continuation"})
        self.assertEqual(self.cards(), ["app-5e55a0b1-1", "app-5e55a0b1-2"])
        first, second = self.card("app-5e55a0b1-1"), self.card("app-5e55a0b1-2")
        self.assertEqual((first["status"], first["done"], first["total"]), ("done", 3, 3))
        self.assertEqual((second["status"], second["done"], self.plan_of(second)), ("active", 0, ["Deck A", "Deck B", "Deck C"]))
        self.assertIn("Title it with its objective", said)

    def test_objective_change_midway_cancels_the_first(self):
        self.call({"items": items(("A", "c"), ("B", "i"), "C"), "scope": "first_task"})
        self.call({"items": items(("X", "i"), "Y", "Z"), "scope": "continuation"})
        self.assertEqual(self.card("app-5e55a0b1-1")["status"], "cancelled")
        self.assertEqual(self.plan_of(self.card("app-5e55a0b1-2")), ["X", "Y", "Z"])

    def test_rewritten_plan_with_nothing_done_stays_on_its_card(self):
        self.call({"items": items(("A", "i"), "B", "C"), "scope": "first_task"})
        self.call({"items": items(("Leer", "i"), "Escribir", "Probar"), "scope": "continuation"})
        self.assertEqual(self.cards(), ["app-5e55a0b1-1"])
        self.assertEqual(self.plan_of(self.card("app-5e55a0b1-1")), ["Leer", "Escribir", "Probar"])

    def test_no_card_mixes_two_objectives(self):
        for n in range(3):  # tres peticiones seguidas, cada una con su lista nueva y su wrap_up
            self.call({"items": items(("O%d paso 1" % n, "i"), "O%d paso 2" % n, "O%d paso 3" % n),
                       "scope": "continuation" if n else "first_task"})
            self.call({"recap": "hecho"}, tool=WRAP)
        for n in range(3):
            d = self.card("app-5e55a0b1-%d" % (n + 1))
            self.assertEqual({s["label"][:2] for s in d["steps"]}, {"O%d" % n})
            self.assertEqual((d["done"], d["total"], d["status"]), (3, 3, "done"))

    def test_cleared_card_is_replaced(self):
        self.call({"items": items(("A", "i"), "B", "C"), "scope": "first_task"})
        self.call({"recap": "hecho"}, tool=WRAP)
        self.cli("clear")
        self.call({"items": items(("A", "c"), ("B", "c"), ("C", "c"), ("D", "i")), "scope": "continuation"})
        self.assertEqual(self.cards(), ["app-5e55a0b1-2"])

    def test_wrap_up_waits_for_running_agents(self):
        self.call({"items": items(("A", "i"), "B", "C"), "scope": "first_task"})
        self.hook("PreToolUse", {"tool_name": "Agent", "tool_use_id": "toolu_1",
                                 "tool_input": {"description": "revisar", "run_in_background": True}})
        self.call({"recap": "hecho"}, tool=WRAP)
        d = self.card("app-5e55a0b1-1")
        self.assertEqual((d["done"], d["total"], d["status"]), (3, 3, "active"))
        self.assertEqual([c["label"] for c in d["children"].values()], ["revisar"])

    def test_child_accept_adds_no_step_on_mirror_card(self):  # hallazgo de F4: 5/5 en vez de 4/4
        self.call({"items": items(("A", "c"), ("Subagente lee b", "i"), "C", "D"), "scope": "first_task"})
        self.hook("PreToolUse", {"tool_name": "Agent", "tool_use_id": "toolu_7",
                                 "tool_input": {"description": "leer b", "run_in_background": False}})
        self.hook("PostToolUse", {"tool_name": "Agent", "tool_use_id": "toolu_7", "tool_input": {}})
        aid = "app-5e55a0b1-1"
        cid = next(iter(self.card(aid)["children"]))
        self.cli("child", "accept", aid, cid)
        d = self.card(aid)
        self.assertEqual(d["children"][cid]["state"], "accepted")
        self.assertEqual((d["done"], d["total"]), (1, 4))
        self.assertNotIn("child:" + cid, d["keys"])
        self.call({"advance": True})
        self.call({"recap": "hecho"}, tool=WRAP)
        d = self.card(aid)
        self.assertEqual((d["done"], d["total"], d["status"]), (4, 4, "done"))

    def test_child_accept_still_counts_on_cli_card(self):
        self.cli("start", "cli", "Plan del CLI", "--total", "3", "--cwd", self.cwd)
        self.cli("child", "add", "cli", "k1", "revisar")
        self.cli("child", "accept", "cli", "k1")
        self.assertEqual(self.card("cli")["done"], 1)

    def test_subagent_and_unknown_shapes_are_ignored(self):
        self.call({"items": items(("A", "i"), "B", "C"), "scope": "first_task"}, agent_id="agent-1")
        self.assertEqual(self.cards(), [])
        self.call({"items": items(("A", "i"), "B", "C"), "scope": "first_task"})
        self.call({"rename": "x"})
        self.call({"items": "no es una lista"})
        d = self.card("app-5e55a0b1-1")
        self.assertEqual((d["done"], d["total"]), (0, 3))
        with open(os.path.join(self.dir, "_hook-errors.log")) as f:
            self.assertEqual(f.read().count("update_plan"), 2)

    def test_wrap_up_without_list_does_nothing(self):
        self.call({"recap": "hecho"}, tool=WRAP)
        self.assertEqual(self.cards(), [])

    def test_title_is_first_step_until_renamed(self):  # no se lee nada de la app de Claude
        said = self.call({"items": items(("Leer el código", "i"), "B", "C"), "scope": "first_task"})
        aid = "app-5e55a0b1-1"
        self.assertEqual(self.card(aid)["title"], "Leer el código")
        self.assertIn("set %s --title" % aid, said)
        self.cli("set", aid, "--title", "Mi título")
        self.call({"items": items(("Otra cosa", "i"), "B", "C"), "scope": "continuation"})
        self.assertEqual(self.card(aid)["title"], "Mi título")

    def test_prompt_says_the_card_mirrors_the_list(self):
        self.call({"items": items(("A", "i"), "B", "C"), "scope": "first_task"})
        r = self.hook("UserPromptSubmit", {"prompt": "sigue"})
        self.assertIn("Active card of this session: app-5e55a0b1-1", r.stdout)
        self.assertIn("mirrors your task list", r.stdout)
        self.assertNotIn("To close a step", r.stdout)

    # ── F3: una sola ficha cuando el modelo también usa `progress start` ──

    def start(self, aid, prompt_id, title="Mi plan"):
        """El modelo abre una ficha con el CLI y el hook ve su Bash (PostToolUse) en el turno prompt_id."""
        self.cli("start", aid, title, "--total", "3", "--cwd", self.cwd)
        self.hook("PostToolUse", {"tool_name": "Bash", "prompt_id": prompt_id,
                                  "tool_input": {"command": '~/.claude/bin/progress start %s "%s" --total 3' % (aid, title)}})

    def test_start_then_list_uses_that_card(self):
        self.start("mi-plan", "p1")
        self.call({"items": items(("A", "i"), "B", "C"), "scope": "first_task"}, prompt_id="p1")
        self.assertEqual(self.cards(), ["mi-plan"])
        d = self.card("mi-plan")
        self.assertEqual((d["title"], self.plan_of(d), d["native"]), ("Mi plan", ["A", "B", "C"], True))
        self.call({"advance": True}, prompt_id="p1")
        self.assertEqual(self.card("mi-plan")["done"], 1)

    def test_list_then_start_moves_the_list(self):
        self.call({"items": items(("A", "c"), ("B", "i"), "C"), "scope": "first_task"}, prompt_id="p1")
        self.hook("PreToolUse", {"tool_name": "Agent", "tool_use_id": "toolu_9",
                                 "tool_input": {"description": "revisar", "run_in_background": True}})
        self.start("mi-plan", "p1")
        self.assertEqual(self.cards(), ["mi-plan"])
        archived = os.listdir(os.path.join(self.dir, "_done"))
        self.assertEqual([n.startswith("app-5e55a0b1-1-") for n in archived], [True])
        d = self.card("mi-plan")
        self.assertEqual((d["title"], self.plan_of(d), d["done"]), ("Mi plan", ["A", "B", "C"], 1))
        self.assertEqual([c["label"] for c in d["children"].values()], ["revisar"])
        self.call({"advance": True}, prompt_id="p1")
        self.assertEqual(self.card("mi-plan")["done"], 2)
        self.assertEqual(self.cards(), ["mi-plan"])

    def test_start_from_another_turn_is_left_alone(self):
        self.start("viejo", "p0", title="Plan viejo")
        self.call({"items": items(("A", "i"), "B", "C"), "scope": "first_task"}, prompt_id="p1")
        self.assertEqual(self.cards(), ["app-5e55a0b1-1", "viejo"])
        self.assertNotIn("native", self.card("viejo"))
        self.start("otro", "p2")  # la lista era de p1: tampoco se mueve
        self.assertEqual(self.cards(), ["app-5e55a0b1-1", "otro", "viejo"])

    def test_terminal_start_writes_no_native_state(self):
        self.start("mi-plan", "p1")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "_native")))

    def test_other_sessions_lists_stay_apart(self):
        self.call({"items": items(("A", "i"), "B", "C"), "scope": "first_task"})
        self.call({"items": items(("X", "i"), "Y", "Z"), "scope": "first_task"},
                  session_id="0ther5e5-0000-4000-8000-000000000002")
        self.call({"advance": True})
        self.assertEqual(self.cards(), ["app-0ther5e5-1", "app-5e55a0b1-1"])
        self.assertEqual(self.card("app-5e55a0b1-1")["done"], 1)
        self.assertEqual(self.card("app-0ther5e5-1")["done"], 0)


if __name__ == "__main__":
    unittest.main()
