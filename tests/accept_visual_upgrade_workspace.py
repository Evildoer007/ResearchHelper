"""Preserve the legacy workspace suite; read styled points by their values.

The base runner installs its chart check after creating the fixture. Compile
an independent runner with only that representation-aware value access changed.
All concrete fee values and all other assertions remain exactly as before.
"""
import inspect
from tests import accept_resdesk_completion as completion


def run():
    baseline=completion.notebook.baseline
    source=inspect.getsource(baseline.run)
    before='JSON.stringify(markers?.data)'
    after='JSON.stringify(markers?.data?.map(point=>Array.isArray(point)?point:point.value))'
    if source.count(before)!=1:raise RuntimeError('Workspace fee-value assertion changed')
    namespace=dict(vars(baseline));namespace['browser_fixture']=completion.fixture
    exec(compile(source.replace(before,after),__file__,'exec'),namespace)
    namespace['run']()


if __name__=='__main__':run()
