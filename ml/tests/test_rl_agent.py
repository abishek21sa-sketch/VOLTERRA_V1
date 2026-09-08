"""Tests for volterra_ml.rl_agent -- masked action selection and a DQN training smoke test.
Self-contained (conftest.py fixtures). Training runs a small number of episodes -- these tests
check the training loop doesn't crash and produces a usable, always-valid-action agent, not that
it reaches any particular performance bar (see examples/train_rl_agent.py for the real evaluation
against held-out episodes and the two baselines)."""

from __future__ import annotations

import numpy as np
import pytest
import torch

from volterra_ml.rl_agent import masked_argmax, train_dqn
from volterra_ml.rl_env import ChargingGuidanceEnv


def test_masked_argmax_respects_the_mask():
    q = torch.tensor([5.0, 1.0, 9.0, 3.0])
    mask = np.array([True, True, False, True])  # index 2 has the highest Q-value but is masked out
    assert masked_argmax(q, mask) == 0  # next-highest among valid indices


def test_train_dqn_smoke_and_masked_actions(diamond_sites, diamond_adjacency, synthetic_models, long_range_vehicle):
    env = ChargingGuidanceEnv(diamond_sites, diamond_adjacency, synthetic_models, long_range_vehicle, "A", "C")

    agent = train_dqn(env, n_episodes=30, min_replay_before_train=10, batch_size=8, seed=0)

    eval_env = ChargingGuidanceEnv(diamond_sites, diamond_adjacency, synthetic_models, long_range_vehicle, "A", "C")
    rng = np.random.default_rng(99)
    for _ in range(20):
        obs = eval_env.reset(rng)
        for _ in range(eval_env.max_steps):
            mask = eval_env.action_mask()
            if not mask.any():
                break
            action = agent.act(eval_env, obs)
            assert mask[action]  # the trained agent never selects a real-infeasible action
            result = eval_env.step(action)
            obs = result.observation
            if result.done or result.truncated or not result.valid:
                break
