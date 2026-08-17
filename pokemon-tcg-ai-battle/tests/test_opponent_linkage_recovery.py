from mage_ptcg.opponent_ingest.recovery import recover_entrypoints
def test_ast_recovers_direct_and_factory_without_importing():
 assert recover_entrypoints('def agent(observation): return [0]', 'x.py')[0]['entrypoint_status']=='VERIFIED_DIRECT_CALLABLE'
 assert recover_entrypoints('def make_agent(deck): return lambda obs:[0]', 'x.py')[0]['entrypoint_status']=='VERIFIED_FACTORY'
def test_ast_recovers_callable_class():
 assert recover_entrypoints('class P:\n def __call__(self, obs): return [0]', 'x.py')[0]['entrypoint_status']=='ADAPTER_REQUIRED'
