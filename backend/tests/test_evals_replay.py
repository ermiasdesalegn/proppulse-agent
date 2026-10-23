"""Regression guard: replay the evaluations from the recording and compare with the committed thresholds.

This is what makes a quality drop fail the build: change the retrieval, the prompts' handling, the graph or the booking logic in a way
that makes the agent worse and this test goes red, with the reason. It needs no network and no model quota.
"""
from argparse import Namespace

import pytest

from evals import report
from evals.__main__ import run
from evals.harness import RECORDING

pytestmark = pytest.mark.skipif(not RECORDING.exists(), reason="no evaluation recording; run `python -m evals --mode record`")


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


async def test_the_agent_is_no_worse_than_the_committed_thresholds(capsys):
    results = await run(Namespace(mode="replay", suites=["retrieval", "scenarios"], only=None))
    status = report.check(results)
    assert status == 0, capsys.readouterr().out + "\n" + report.console(results)


async def test_replay_never_reaches_the_network():
    results = await run(Namespace(mode="replay", suites=["retrieval"], only=None))
    assert results["retrieval"]["live_calls"] == 0
    assert results["retrieval"]["summary"]["crashed"] == 0, "a crashed case usually means a prompt changed and its answer is not recorded: re-record"
