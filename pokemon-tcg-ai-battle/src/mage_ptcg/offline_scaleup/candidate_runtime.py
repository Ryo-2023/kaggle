"""Candidate-side teacher adapters with fail-closed action capture."""
from __future__ import annotations
from collections import Counter
from dataclasses import replace
import hashlib, json, os, sys
from pathlib import Path
from typing import Any, Mapping

from mage_ptcg.decision_state import build_decision_state
from mage_ptcg.student.dataset import RuleBCExample, build_rule_bc_example


FAMILY_DECK_BINDINGS = {
    "MEGA_LUCARIO_EX": "a0e78dd4b5731f95ff14686ca5fa4c31fcd23ef7868c2ccc262fb50eaa450b39",
    "MEGA_ABOMASNOW_EX": "cb0c1b3e0e87e77b270719f387d7d0fe11ae3807b6b461a2e498c77ca1813895",
    "ALAKAZAM": "d3d6354cd3de3ab71677894265d46a07434ec7b0a199064b0de143206e09fd14",
}
INTERNAL_FAMILY_LOADER = "family_specific_internal_v1"
STUDENT_V2_LOADER = "student_v2_internal_v1"
POLICY_LEARNING_LOADER = "policy_learning_actor_critic_v1"
MAX_VISIBLE_HISTORY = 32


class CandidateRuntimeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None: super().__init__(message); self.code=code


def _canonical(value: object) -> str: return json.dumps(value,ensure_ascii=False,allow_nan=False,sort_keys=True,separators=(",",":"))
def _digest(value: object) -> str: return hashlib.sha256(_canonical(value).encode()).hexdigest()
def _deck_fingerprint(deck: list[int]) -> str: return hashlib.sha256(("deck-multiset\0"+_canonical(sorted(Counter(deck).items()))).encode()).hexdigest()
def _sha256_file(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()


class CandidateRuntimeAdapter:
    adapter_type="abstract"
    def __init__(self, entry: Mapping[str,Any]) -> None:
        self.entry=dict(entry); self.teacher_identity=str(entry["opponent_id"]); self.teacher_type=str(entry["opponent_type"]); self.teacher_trust=str(entry["teacher_trust"]); self.runtime_fingerprint=str(entry["runtime_fingerprint"]); self.deck_fingerprint=str(entry["deck_fingerprint"]); self.family_id=entry.get("family_id"); self._agent=None; self._visible_history: list[str]=[]; self.last_fallback_reason: str | None = None
        # ``capture`` intentionally persists no telemetry row for a legal
        # empty answer to an optional prompt.  Without these counters a real
        # Rule-v0 delegation on that path would be indistinguishable from
        # "no delegation happened", because both leave zero decision rows.
        self.decision_counters: Counter[str] = Counter()

    def reset_decision_counters(self) -> None:
        self.decision_counters = Counter()
    @property
    def telemetry_capabilities(self) -> dict[str,bool]: return {"rule_score":False,"family_score":False,"strategy_score":False,"variant_score":False,"fired_rule_ids":False,"target_selector":False,"fallback":False}
    def prepare(self, deck: list[int]): raise NotImplementedError
    def decide(self, observation: object) -> list[int]:
        if self._agent is None: raise CandidateRuntimeError("TEACHER_RUNTIME_LOAD_FAILURE","adapter is not prepared")
        # A deck request starts a game.  State belongs to this candidate seat
        # only and is never allowed to survive into the next episode.
        if isinstance(observation, Mapping) and observation.get("select") is None:
            self._visible_history = []
            self.last_fallback_reason = None
        try: choice=self._agent(observation)
        except Exception as exc: raise CandidateRuntimeError("TEACHER_DECISION_FAILURE",str(exc)[:300]) from exc
        if not isinstance(choice,list) or any(type(index) is not int for index in choice): raise CandidateRuntimeError("TEACHER_ILLEGAL_ACTION","teacher response is not an index list")
        return choice
    def capture(self, observation: object, choice: list[int], *, game_id: str, candidate_side: int, deck: list[int]) -> tuple[dict[str,Any],dict[str,Any]] | None:
        # CABT may invoke the agent for a non-selectable bootstrap/terminal
        # observation.  It has no ActionKey domain, therefore it is not a
        # teacher decision and must remain an explicit null capture.
        if not isinstance(observation,dict) or not isinstance(observation.get("select"),Mapping):
            return None
        history=tuple(self._visible_history)
        try: state=build_decision_state(observation,visible_history=history)
        except Exception as exc: raise CandidateRuntimeError("TEACHER_ACTION_MAPPING_FAILURE",str(exc)[:300]) from exc
        by_index={action.option_index:action for action in state.legal_actions}
        select=observation.get("select")
        optional_prompt=isinstance(select,Mapping) and select.get("minCount")==0
        if optional_prompt: self.decision_counters["optional_prompt_count"]+=1
        if not choice and optional_prompt:
            # Legal empty answer.  Count it, and separately count the case
            # where this seat only produced it through a Rule-v0 delegation,
            # so a silent runtime defect cannot hide behind an unrecorded row.
            self.decision_counters["optional_declined_count"]+=1
            if self.last_fallback_reason is not None:
                self.decision_counters["uncaptured_fallback_count"]+=1
                self.decision_counters["actual_fallback_decisions"]+=1
            return None
        if not choice or len(choice)!=len(set(choice)) or not set(choice).issubset(by_index): raise CandidateRuntimeError("TEACHER_ILLEGAL_ACTION","teacher selected an action outside legal candidates")
        try: baseline=build_rule_bc_example(observation,deck=deck,source_id=game_id,source_revision=self.runtime_fingerprint,visible_history=history)
        except Exception as exc: raise CandidateRuntimeError("TEACHER_ACTION_MAPPING_FAILURE",str(exc)[:300]) from exc
        selected=tuple(by_index[index].action_key.digest for index in choice)
        example=replace(baseline,target_action_digests=selected,teacher_ranking=tuple((action["digest"],0) for action in baseline.legal_actions),fallback_used=False)
        # Only public state and opaque ActionKey digests cross the decision
        # boundary.  Never retain raw observations or own/private card IDs.
        # The recurrent actor encodes exactly the latest 32 public events.
        # Retaining more changes neither model input nor telemetry semantics,
        # but would violate the runtime's bounded-history contract on long
        # CABT games.
        self._visible_history = [
            *self._visible_history[-(MAX_VISIBLE_HISTORY - 1):],
            _digest({"public_state":state.actor_view.public_state,"selected_action_digests":selected}),
        ]
        self.decision_counters["captured_decision_count"]+=1
        if self.last_fallback_reason is not None: self.decision_counters["actual_fallback_decisions"]+=1
        public_state = state.actor_view.public_state
        telemetry={"schema_version":"offline-scaleup-teacher-decision-v1","episode_id":game_id,"game_id":game_id,"turn":public_state.get("turn") if isinstance(public_state, Mapping) else None,"phase":"OBSERVED","candidate_side":candidate_side,"teacher_identity":self.teacher_identity,"teacher_type":self.teacher_type,"teacher_trust":self.teacher_trust,"runtime_fingerprint":self.runtime_fingerprint,"deck_fingerprint":self.deck_fingerprint,"family_id":self.family_id,"strategy_profile":None,"variant_id":None,"state_fingerprint":example.example_id,"legal_action_candidates":list(example.legal_actions),"selected_action":list(selected),"selected_action_key":list(selected),"selected_candidate_index":choice,"rule_v0_score":None,"family_score":None,"strategy_score":None,"variant_score":None,"fired_rule_ids":None,"target_selector_result":None,"fallback_used":self.last_fallback_reason is not None,"fallback_reason":self.last_fallback_reason,"legality_result":True,"decision_latency_us":None,"source_game_result":None,"telemetry_capabilities":self.telemetry_capabilities,"provenance":{"adapter_type":self.adapter_type,"source_revision":example.source_revision},"rule_bc_example":example.to_dict()}
        return example.to_dict(),telemetry
    def close(self) -> None: self._agent=None


class RuleV0CandidateAdapter(CandidateRuntimeAdapter):
    adapter_type="rule_v0_candidate_v1"
    def prepare(self, deck: list[int]):
        if _deck_fingerprint(deck)!=self.deck_fingerprint: raise CandidateRuntimeError("TEACHER_DECK_BINDING_FAILURE","Rule v0 deck fingerprint mismatch")
        from main import make_rule_agent
        self._agent=make_rule_agent(deck=deck); return self

    def capture(self, observation: object, choice: list[int], *, game_id: str, candidate_side: int, deck: list[int]):
        captured = super().capture(observation, choice, game_id=game_id, candidate_side=candidate_side, deck=deck)
        if captured is None:
            return None
        example, telemetry = captured
        # The Rule-v0 candidate's selected legal action is its own proposal.
        # Store it explicitly so a teacher-policy holdout can evaluate the
        # proposal-input model under the same observable feature contract.
        if len(example.get("target_action_digests", [])) == 1:
            example = dict(example)
            example["rule_proposal_digests"] = list(example["target_action_digests"])
            telemetry["rule_proposal_digests"] = example["rule_proposal_digests"]
        return example, telemetry


class FamilySpecificCandidateAdapter(CandidateRuntimeAdapter):
    adapter_type="family_specific_candidate_v1"
    def prepare(self, deck: list[int]):
        if not isinstance(self.family_id,str) or not self.family_id: raise CandidateRuntimeError("TEACHER_DECK_BINDING_FAILURE","Family ID missing")
        if FAMILY_DECK_BINDINGS.get(self.family_id) != self.deck_fingerprint: raise CandidateRuntimeError("TEACHER_DECK_BINDING_FAILURE","Family ID is not bound to this canonical deck fingerprint")
        if _deck_fingerprint(deck)!=self.deck_fingerprint: raise CandidateRuntimeError("TEACHER_DECK_BINDING_FAILURE","Family deck fingerprint mismatch")
        if self.entry.get("validation_status") != "VALIDATED" or self.teacher_trust not in {"TRUSTED","LIMITED"}: raise CandidateRuntimeError("TEACHER_RUNTIME_LOAD_FAILURE","Family runtime lacks validated eligible trust status")
        if not self.runtime_fingerprint: raise CandidateRuntimeError("TEACHER_RUNTIME_LOAD_FAILURE","Family runtime fingerprint missing")
        primary=self.entry.get("provenance",{}).get("primary_ids")
        if not isinstance(primary,list) or any(type(value) is not int for value in primary): raise CandidateRuntimeError("TEACHER_DECK_BINDING_FAILURE","Family primary IDs missing")
        root=str(self.entry.get("source_path",""))
        if not root or not Path(root).exists(): raise CandidateRuntimeError("TEACHER_RUNTIME_LOAD_FAILURE","Family source artifact unavailable")
        if root not in sys.path: sys.path.insert(0,root)
        try:
            from family_agent.agent import FamilySpecificAgent
            from family_agent.strategy import load_intended_strategy_registry
            from main import make_rule_agent
            deck_id=str(self.entry["deck_id"]); weights=load_intended_strategy_registry([deck_id])[deck_id]["intended_strategy_weights"]
            self._agent=FamilySpecificAgent(self.family_id,deck_id,deck,primary,make_rule_agent(deck=deck),weights).as_agent()
        except Exception as exc: raise CandidateRuntimeError("TEACHER_RUNTIME_LOAD_FAILURE",str(exc)[:300]) from exc
        return self


class InternalFamilyCandidateAdapter(CandidateRuntimeAdapter):
    """Trusted repository-native Family policy with no Rule-v0 fallback."""
    adapter_type="family_specific_internal_v1"

    @property
    def telemetry_capabilities(self) -> dict[str,bool]:
        return {"rule_score":False,"family_score":True,"strategy_score":False,"variant_score":True,"fired_rule_ids":True,"target_selector":False,"fallback":True}

    def prepare(self, deck: list[int]):
        if not isinstance(self.family_id,str) or not self.family_id:
            raise CandidateRuntimeError("TEACHER_DECK_BINDING_FAILURE","Family ID missing")
        if _deck_fingerprint(deck)!=self.deck_fingerprint:
            raise CandidateRuntimeError("TEACHER_DECK_BINDING_FAILURE","Family deck fingerprint mismatch")
        config=self.entry.get("provenance",{}).get("family_config")
        if not isinstance(config,Mapping) or config.get("family_id") != self.family_id:
            raise CandidateRuntimeError("TEACHER_DECK_BINDING_FAILURE","Family config is missing or mismatched")
        try:
            from mage_ptcg.family_agents import ConfigDrivenFamilyAgent
            self._family_agent=ConfigDrivenFamilyAgent(deck=deck,config=config)
            self._agent=self._family_agent.as_agent()
        except Exception as exc:
            raise CandidateRuntimeError("TEACHER_RUNTIME_LOAD_FAILURE",str(exc)[:300]) from exc
        return self

    def capture(self, observation: object, choice: list[int], *, game_id: str, candidate_side: int, deck: list[int]):
        if isinstance(observation, Mapping) and observation.get("select") is None:
            # CABT's deck request precedes every selectable prompt.  Seed a
            # PPO actor here (rather than at worker construction) so each
            # recorded game explores independently yet retries are replayable.
            self._policy.set_episode_seed(game_id=game_id, candidate_side=candidate_side)
        captured=super().capture(observation,choice,game_id=game_id,candidate_side=candidate_side,deck=deck)
        if captured is None:
            return None
        example, telemetry=captured
        state=self._family_agent.last_telemetry
        if state.fallback_used:
            raise CandidateRuntimeError("TEACHER_DECISION_FAILURE","internal Family policy reported a fallback")
        telemetry.update({"family_score":state.family_score,"strategy_score":state.strategy_score,"variant_score":state.variant_score,"fired_rule_ids":state.fired_rule_ids,"fallback_used":False,"fallback_reason":None})
        return example,telemetry


class StudentV2CandidateAdapter(CandidateRuntimeAdapter):
    """Trusted repository-native Student v2 checkpoint with no Rule-v0 fallback."""
    adapter_type="student_v2_candidate_v1"

    @property
    def telemetry_capabilities(self) -> dict[str,bool]:
        return {"rule_score":False,"family_score":False,"strategy_score":False,"variant_score":False,"fired_rule_ids":False,"target_selector":False,"fallback":True}

    def prepare(self, deck: list[int]):
        if _deck_fingerprint(deck)!=self.deck_fingerprint:
            raise CandidateRuntimeError("TEACHER_DECK_BINDING_FAILURE","Student v2 deck fingerprint mismatch")
        provenance=self.entry.get("provenance",{})
        model_dir=provenance.get("model_dir")
        if not isinstance(model_dir,str) or not model_dir or not Path(model_dir).is_dir():
            raise CandidateRuntimeError("TEACHER_RUNTIME_LOAD_FAILURE","Student v2 model directory unavailable")
        expected_digest=provenance.get("model_sha256")
        if not isinstance(expected_digest,str) or len(expected_digest)!=64:
            raise CandidateRuntimeError("TEACHER_RUNTIME_LOAD_FAILURE","Student v2 model digest is missing")
        checkpoint_path=Path(model_dir)/"best.pt"
        if not checkpoint_path.is_file() or _sha256_file(checkpoint_path)!=expected_digest:
            raise CandidateRuntimeError("TEACHER_RUNTIME_LOAD_FAILURE","Student v2 checkpoint digest mismatch")
        try:
            from mage_ptcg.offline_scaleup.student_v2_runtime import StudentV2CandidatePolicy, load_candidate_ranker
            device=str(provenance.get("device","cpu"))
            model,_summary=load_candidate_ranker(Path(model_dir),device)
            self._policy=StudentV2CandidatePolicy(model=model,device=device,deck=deck)
            self._agent=self._policy.as_agent()
        except Exception as exc:
            raise CandidateRuntimeError("TEACHER_RUNTIME_LOAD_FAILURE",str(exc)[:300]) from exc
        return self

    def capture(self, observation: object, choice: list[int], *, game_id: str, candidate_side: int, deck: list[int]):
        captured=super().capture(observation,choice,game_id=game_id,candidate_side=candidate_side,deck=deck)
        if captured is None:
            return None
        example,telemetry=captured
        telemetry.update({"fallback_used":False,"fallback_reason":None})
        return example,telemetry


class PolicyLearningCandidateAdapter(CandidateRuntimeAdapter):
    """Candidate-only recurrent legal-action actor-critic runtime."""
    adapter_type="policy_learning_actor_critic_v1"

    @property
    def telemetry_capabilities(self) -> dict[str,bool]:
        return {"rule_score":False,"family_score":False,"strategy_score":False,"variant_score":False,"fired_rule_ids":False,"target_selector":False,"fallback":False}

    def prepare(self, deck: list[int]):
        self._visible_history=[]
        self._last_rule_proposal_digests: list[str] | None = None
        if _deck_fingerprint(deck)!=self.deck_fingerprint:
            raise CandidateRuntimeError("TEACHER_DECK_BINDING_FAILURE","policy-learning deck fingerprint mismatch")
        provenance=self.entry.get("provenance",{})
        model_dir=provenance.get("model_dir")
        expected_digest=provenance.get("model_sha256")
        if not isinstance(model_dir,str) or not Path(model_dir).is_dir() or not isinstance(expected_digest,str) or len(expected_digest)!=64:
            raise CandidateRuntimeError("TEACHER_RUNTIME_LOAD_FAILURE","policy-learning model provenance is invalid")
        checkpoint=Path(model_dir)/"best.pt"
        if not checkpoint.is_file() or _sha256_file(checkpoint)!=expected_digest or self.runtime_fingerprint != expected_digest:
            raise CandidateRuntimeError("TEACHER_RUNTIME_LOAD_FAILURE","policy-learning checkpoint digest mismatch")
        try:
            from mage_ptcg.policy_learning.runtime import load_runtime_policy
            from main import make_rule_agent
            # The action mode is a recorded property of this population entry.
            # Older entries predate the field; they were collected under the
            # deterministic ranking, so default to it rather than guessing
            # from the checkpoint schema.
            action_mode=str(provenance.get("action_mode","argmax"))
            self._policy,_summary=load_runtime_policy(Path(model_dir),device=str(provenance.get("device","cpu")),deck=deck,action_mode=action_mode)
            self.action_mode=action_mode
            self._agent=self._policy.as_agent()
            self._rule_v0_agent=make_rule_agent(deck=deck)
            self._policy.set_rule_proposal_agent(self._rule_v0_agent)
        except Exception as exc:
            raise CandidateRuntimeError("TEACHER_RUNTIME_LOAD_FAILURE",str(exc)[:300]) from exc
        return self

    def decide(self, observation: object) -> list[int]:
        # The runtime sees exactly the same prior public-event digest sequence
        # that will be persisted by ``capture`` after this decision.
        self._visible_history = self._visible_history[-MAX_VISIBLE_HISTORY:]
        self._policy.set_visible_history(tuple(self._visible_history))
        self.last_fallback_reason=None
        self._last_rule_proposal_digests = None
        # Record the Rule-v0 proposal independently of whether this candidate
        # uses it as a model feature.  It is an observable legal action only,
        # and allows a later AWR+proposal experiment without inventing labels.
        if isinstance(observation, Mapping) and isinstance(observation.get("select"), Mapping):
            select = observation["select"]
            if select.get("minCount") == select.get("maxCount") == 1:
                try:
                    proposed = self._rule_v0_agent(observation)
                    state = build_decision_state(observation, visible_history=tuple(self._visible_history))
                    matched = [action.action_key.digest for action in state.legal_actions
                               if isinstance(proposed, list) and len(proposed) == 1 and action.option_index == proposed[0]]
                    if len(matched) == 1:
                        self._last_rule_proposal_digests = matched
                except Exception:
                    # This is not a candidate-action fallback: capture keeps
                    # the missing proposal explicit, and the proposal model's
                    # dataset contract rejects incomplete collections.
                    self._last_rule_proposal_digests = None
        try:
            return super().decide(observation)
        except CandidateRuntimeError as exc:
            # Numerical corruption is never hidden by a fallback.  Other
            # unsupported decision shapes may use the safe Rule-v0 path, and
            # the reason is carried through trajectory and result telemetry.
            if "non-finite" in str(exc).lower():
                raise
            self.last_fallback_reason="RULE_V0_POLICY_RUNTIME:" + str(exc)[:180]
            try:
                choice=self._rule_v0_agent(observation)
            except Exception as fallback_exc:
                raise CandidateRuntimeError("TEACHER_DECISION_FAILURE", "Rule v0 fallback failed") from fallback_exc
            if not isinstance(choice,list) or any(type(index) is not int for index in choice):
                raise CandidateRuntimeError("TEACHER_ILLEGAL_ACTION","Rule v0 fallback response is not an index list")
            return choice

    def capture(self, observation: object, choice: list[int], *, game_id: str, candidate_side: int, deck: list[int]):
        captured=super().capture(observation,choice,game_id=game_id,candidate_side=candidate_side,deck=deck)
        if captured is None:
            return None
        example,telemetry=captured
        trace=self._policy.last_decision_trace or {}
        if self.last_fallback_reason is None and trace.get("status") == "selected":
            example=dict(example)
            for key in ("behavior_log_probability","actor_policy_version","vocabulary_hash"):
                value=trace.get(key)
                if value is None:
                    raise CandidateRuntimeError("TEACHER_DECISION_FAILURE",f"policy-learning trace lacks {key}")
                example[key]=value
            # The categorical actor has no action-set probability for a
            # multi-select choice.  Persist that fact so PPO can reject the
            # whole episode rather than silently treating Rule/Top-k actions
            # as on-policy samples.
            example["behavior_log_probability_kind"] = trace.get("behavior_log_probability_kind")
            example["actor_action_mode"] = trace.get("actor_action_mode")
            example["ppo_eligible"] = trace.get("ppo_eligible") is True
            example["policy_confidence"] = trace.get("policy_confidence")
            if trace.get("rule_proposal_digests") is not None:
                example["rule_proposal_digests"] = trace["rule_proposal_digests"]
            elif self._last_rule_proposal_digests is not None:
                example["rule_proposal_digests"] = self._last_rule_proposal_digests
            telemetry.update({key: example[key] for key in ("behavior_log_probability","actor_policy_version","vocabulary_hash",
                                                             "behavior_log_probability_kind","actor_action_mode","ppo_eligible",
                                                             "policy_confidence")})
            if "rule_proposal_digests" in example:
                telemetry["rule_proposal_digests"] = example["rule_proposal_digests"]
        if self.last_fallback_reason is not None:
            example_dict=dict(example); rule=replace(RuleBCExample.from_dict(example_dict),fallback_used=True)
            example_dict=rule.to_dict()
            for key in ("behavior_log_probability","actor_policy_version","vocabulary_hash","rule_proposal_digests",
                        "behavior_log_probability_kind","actor_action_mode","ppo_eligible","policy_confidence"):
                if key in example:
                    example_dict[key]=example[key]
            example=example_dict
            telemetry["rule_bc_example"]=example_dict
        telemetry.update({"fallback_used":self.last_fallback_reason is not None,"fallback_reason":self.last_fallback_reason})
        if self.last_fallback_reason is not None:
            example["ppo_eligible"] = False
            telemetry["ppo_eligible"] = False
        telemetry["rule_bc_example"] = example
        return example,telemetry


def adapter_for(entry: Mapping[str,Any]) -> CandidateRuntimeAdapter:
    loader=entry.get("loader")
    if loader=="rule_v0": return RuleV0CandidateAdapter(entry)
    if loader=="family_specific_external_v1": return FamilySpecificCandidateAdapter(entry)
    if loader==INTERNAL_FAMILY_LOADER: return InternalFamilyCandidateAdapter(entry)
    if loader==STUDENT_V2_LOADER: return StudentV2CandidateAdapter(entry)
    if loader==POLICY_LEARNING_LOADER: return PolicyLearningCandidateAdapter(entry)
    raise CandidateRuntimeError("TEACHER_RUNTIME_LOAD_FAILURE",f"candidate adapter unsupported for loader {loader!r}")


def write_trajectory(path: Path, decisions: list[dict[str,Any]], metadata: Mapping[str,Any]) -> str:
    path.parent.mkdir(parents=True,exist_ok=True); temporary=path.with_name(path.name+".tmp")
    with temporary.open("w",encoding="utf-8") as handle:
        handle.write(_canonical({"schema_version":"offline-scaleup-teacher-trajectory-v1","metadata":dict(metadata)})+"\n")
        for decision in decisions: handle.write(_canonical(decision)+"\n")
    os.replace(temporary,path); digest=hashlib.sha256(path.read_bytes()).hexdigest(); return digest
