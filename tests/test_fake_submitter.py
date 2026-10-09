import unittest
from simulation_demo import (scenario_approval_and_repeat,scenario_answer_reuse,scenario_challenge,
                             scenario_unknown,scenario_lost_response,scenario_failed_and_live)


class FakeSubmitterTests(unittest.TestCase):
    def test_approval_repeat(self): scenario_approval_and_repeat()
    def test_answer_reuse(self): scenario_answer_reuse()
    def test_challenge_restart(self): scenario_challenge()
    def test_unknown_no_retry(self): scenario_unknown()
    def test_committed_response_lost(self): scenario_lost_response()
    def test_failed_and_live_refusal(self): scenario_failed_and_live()


if __name__=='__main__': unittest.main()
