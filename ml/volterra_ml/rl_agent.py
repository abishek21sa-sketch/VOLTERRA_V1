"""A small DQN (Deep Q-Network) for the charging-guidance environment. `torch` has been a declared
dependency of this package since Phase 6 specifically earmarked for this phase (see
../pyproject.toml and ../README.md) -- honoring that rather than substituting a lighter-weight
tabular method, even though the real corridor's state/action space (4 real sites) is small enough
that tabular Q-learning would also work; a network-valued Q-function additionally leaves room to
extend to the full 17-site real network later without an architecture change, which a table
wouldn't.

Standard DQN: experience replay, a target network updated by Polyak averaging, epsilon-greedy
exploration with linear decay, action masking applied at both action-selection and target-value
computation so invalid real moves are never selected or bootstrapped from.
"""

from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass

import numpy as np
import torch
from torch import nn

from .rl_env import ChargingGuidanceEnv


class QNetwork(nn.Module):
    def __init__(self, obs_dim: int, n_actions: int, hidden: int = 64):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(obs_dim, hidden), nn.ReLU(),
            nn.Linear(hidden, hidden), nn.ReLU(),
            nn.Linear(hidden, n_actions),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


@dataclass
class Transition:
    obs: np.ndarray
    action: int
    reward: float
    next_obs: np.ndarray
    done: bool
    next_mask: np.ndarray


class ReplayBuffer:
    def __init__(self, capacity: int = 20_000):
        self.buffer: deque[Transition] = deque(maxlen=capacity)

    def push(self, t: Transition) -> None:
        self.buffer.append(t)

    def sample(self, batch_size: int, rng: random.Random) -> list[Transition]:
        return rng.sample(self.buffer, batch_size)

    def __len__(self) -> int:
        return len(self.buffer)


def masked_argmax(q_values: torch.Tensor, mask: np.ndarray) -> int:
    masked = q_values.clone()
    masked[~torch.as_tensor(mask)] = -float("inf")
    return int(torch.argmax(masked).item())


@dataclass
class DQNAgent:
    q_net: QNetwork
    n_actions: int

    def act(self, env: ChargingGuidanceEnv, obs: np.ndarray) -> int:
        """A `policy_fn` compatible with `run_episode` -- greedy w.r.t. the trained Q-network,
        masked to real feasible actions."""
        with torch.no_grad():
            q = self.q_net(torch.as_tensor(obs, dtype=torch.float32))
        return masked_argmax(q, env.action_mask())


def train_dqn(env: ChargingGuidanceEnv, *, n_episodes: int = 2000, hidden: int = 64,
              gamma: float = 0.95, lr: float = 1e-3, batch_size: int = 64,
              replay_capacity: int = 20_000, min_replay_before_train: int = 200,
              epsilon_start: float = 1.0, epsilon_end: float = 0.05, epsilon_decay_episodes: int = 1500,
              target_update_tau: float = 0.01, seed: int = 0) -> DQNAgent:
    """Trains a DQN agent on `env` over `n_episodes` real corridor trips, each with a freshly
    sampled realized-congestion draw (see rl_env.py). Returns the trained agent; caller evaluates
    it via `run_episode` against fresh, held-out episodes (a different `rng` seed) alongside the
    static and greedy baselines -- see rl_baselines.py and examples/train_rl_agent.py."""
    torch.manual_seed(seed)
    py_rng = random.Random(seed)
    np_rng = np.random.default_rng(seed)

    q_net = QNetwork(env.observation_dim, env.n_sites, hidden=hidden)
    target_net = QNetwork(env.observation_dim, env.n_sites, hidden=hidden)
    target_net.load_state_dict(q_net.state_dict())
    optimizer = torch.optim.Adam(q_net.parameters(), lr=lr)
    buffer = ReplayBuffer(replay_capacity)

    agent = DQNAgent(q_net=q_net, n_actions=env.n_sites)

    for episode in range(n_episodes):
        epsilon = epsilon_end + (epsilon_start - epsilon_end) * max(0.0, 1.0 - episode / epsilon_decay_episodes)
        obs = env.reset(np_rng)
        for _ in range(env.max_steps):
            mask = env.action_mask()
            valid_actions = np.flatnonzero(mask)
            if valid_actions.size == 0:
                break  # stranded -- no real feasible next hop, episode ends without reaching destination
            if py_rng.random() < epsilon:
                action = int(py_rng.choice(valid_actions))
            else:
                with torch.no_grad():
                    q = q_net(torch.as_tensor(obs, dtype=torch.float32))
                action = masked_argmax(q, mask)

            result = env.step(action)
            next_mask = env.action_mask() if not (result.done or result.truncated or not result.valid) else np.zeros(env.n_sites, dtype=bool)
            buffer.push(Transition(obs, action, result.reward, result.observation, result.done, next_mask))
            obs = result.observation

            if len(buffer) >= min_replay_before_train:
                batch = buffer.sample(batch_size, py_rng)
                _train_step(q_net, target_net, optimizer, batch, gamma)
                _soft_update(target_net, q_net, target_update_tau)

            if result.done or result.truncated or not result.valid:
                break

    return agent


def _train_step(q_net: QNetwork, target_net: QNetwork, optimizer: torch.optim.Optimizer,
                 batch: list[Transition], gamma: float) -> None:
    obs = torch.as_tensor(np.stack([t.obs for t in batch]), dtype=torch.float32)
    actions = torch.as_tensor([t.action for t in batch], dtype=torch.long)
    rewards = torch.as_tensor([t.reward for t in batch], dtype=torch.float32)
    next_obs = torch.as_tensor(np.stack([t.next_obs for t in batch]), dtype=torch.float32)
    dones = torch.as_tensor([t.done for t in batch], dtype=torch.float32)
    next_masks = torch.as_tensor(np.stack([t.next_mask for t in batch]))

    q_values = q_net(obs).gather(1, actions.unsqueeze(1)).squeeze(1)

    with torch.no_grad():
        next_q = target_net(next_obs)
        next_q[~next_masks] = -float("inf")
        # A terminal/stranded next state has no valid actions at all -- its masked max would be
        # -inf, which must contribute 0 bootstrap value, not propagate -inf into the TD target.
        no_valid_next = ~next_masks.any(dim=1)
        next_q_max = next_q.max(dim=1).values
        next_q_max = torch.where(no_valid_next, torch.zeros_like(next_q_max), next_q_max)
        target = rewards + gamma * (1.0 - dones) * next_q_max

    loss = nn.functional.mse_loss(q_values, target)
    optimizer.zero_grad()
    loss.backward()
    optimizer.step()


def _soft_update(target_net: nn.Module, source_net: nn.Module, tau: float) -> None:
    with torch.no_grad():
        for target_param, source_param in zip(target_net.parameters(), source_net.parameters()):
            target_param.mul_(1.0 - tau).add_(source_param, alpha=tau)
