"""Trusted glue: binds the bundled policy content file to a runnable factory.

``policy_loader.py`` is, together with ``main.py``, part of the archive's
trusted *entrypoint* set: ``package.py`` hashes both of their bytes into the
archive's ``entrypoint_contract_id`` (see ``derive_entrypoint_contract_id``),
but -- unlike the file(s) listed in the manifest's ``policy_members`` -- its
own bytes are never hashed into ``policy_identity``.  Its job is only to
construct a :class:`~mage_ptcg.meta_specialist.runtime.StepLogitPolicyFactory`
from whichever policy content file(s) the bundle actually declares, given the
frozen ``policy_identity`` the manifest recorded for them.

``main.py`` calls :func:`load_policy_factory` after it has already inserted
this file's own directory onto ``sys.path`` (see ``main.py``'s
``_bundle_root`` helper), so the bare ``import rule_policy_v1`` below
resolves the sibling file the archive ships alongside this one.
"""

from __future__ import annotations

from mage_ptcg.meta_specialist.runtime import StepLogitPolicyFactory

import rule_policy_v1


def load_policy_factory(*, policy_identity: str) -> StepLogitPolicyFactory:
    """Return the P0 static rule policy factory bound to ``policy_identity``.

    ``policy_identity`` must be the exact value
    ``entrypoint.load_specialist_bundle`` already independently recomputed
    from ``rule_policy_v1.py``'s on-disk bytes and cross-checked against the
    bundle manifest -- this function does not repeat that verification, it
    only threads the already-verified identity through to every policy
    object this factory produces.
    """
    return rule_policy_v1.UniformLegalPolicyFactory(policy_identity=policy_identity)


__all__ = ["load_policy_factory"]
