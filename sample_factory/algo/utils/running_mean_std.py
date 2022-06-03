"""
PyTorch module that keeps track of tensor statistics and uses it to normalize data.
All credit goes to https://github.com/Denys88/rl_games (only slightly changed here)
Thanks a lot, great module!
"""
from typing import Dict, Final, Union

import gym
import torch
import torch.nn as nn
from torch import Tensor
from torch.jit import ScriptModule, RecursiveScriptModule

from sample_factory.utils.utils import log


_NORM_EPS = 1e-5


# noinspection PyAttributeOutsideInit
class RunningMeanStdInPlace(nn.Module):
    def __init__(self, input_shape, epsilon=_NORM_EPS, per_channel=False, norm_only=False):
        super().__init__()
        log.debug('RunningMeanStd input shape: %r', input_shape)
        self.input_shape: Final = input_shape
        self.epsilon: Final[float] = epsilon

        self.norm_only: Final[bool] = norm_only
        self.per_channel: Final[bool] = per_channel

        if per_channel:
            if len(self.input_shape) == 3:
                self.axis = [0, 2, 3]
            if len(self.input_shape) == 2:
                self.axis = [0, 2]
            if len(self.input_shape) == 1:
                self.axis = [0]
            shape = self.input_shape[0]
        else:
            self.axis = [0]
            shape = input_shape

        self.register_buffer('running_mean', torch.zeros(shape, dtype=torch.float64))
        self.register_buffer('running_var', torch.ones(shape, dtype=torch.float64))
        self.register_buffer('count', torch.ones([1], dtype=torch.float64))

    @staticmethod
    @torch.jit.script
    def _update_mean_var_count_from_moments(mean: Tensor, var: Tensor, count: Tensor, batch_mean: Tensor, batch_var: Tensor, batch_count: int):
        delta = batch_mean - mean
        tot_count = count + batch_count

        new_mean = mean + delta * batch_count / tot_count
        m_a = var * count
        m_b = batch_var * batch_count
        M2 = m_a + m_b + (delta ** 2) * count * batch_count / tot_count
        new_var = M2 / tot_count
        return new_mean, new_var, tot_count

    def forward(self, x: Tensor) -> None:
        """Normalizes in-place! This means this function modifies the input tensor and returns nothing."""
        if self.training:
            batch_count = x.size()[0]
            mean = x.mean(self.axis)  # along channel axis
            var = x.var(self.axis)
            self.running_mean[:], self.running_var[:], self.count[:] = self._update_mean_var_count_from_moments(
                self.running_mean, self.running_var, self.count, mean, var, batch_count,
            )

        # change shape
        if self.per_channel:
            if len(self.input_shape) == 3:
                current_mean = self.running_mean.view([1, self.input_shape[0], 1, 1]).expand_as(x)
                current_var = self.running_var.view([1, self.input_shape[0], 1, 1]).expand_as(x)
            elif len(self.input_shape) == 2:
                current_mean = self.running_mean.view([1, self.input_shape[0], 1]).expand_as(x)
                current_var = self.running_var.view([1, self.input_shape[0], 1]).expand_as(x)
            elif len(self.input_shape) == 1:
                current_mean = self.running_mean.view([1, self.input_shape[0]]).expand_as(x)
                current_var = self.running_var.view([1, self.input_shape[0]]).expand_as(x)
            else:
                raise RuntimeError(f'RunningMeanStd input shape {self.input_shape} not supported')
        else:
            current_mean = self.running_mean
            current_var = self.running_var

        if self.norm_only:
            x.mul_(1.0 / torch.sqrt(current_var.float() + self.epsilon))
        else:
            x.sub_(current_mean.float())
            x.mul_(1.0 / torch.sqrt(current_var.float() + self.epsilon))
            x.clamp_(-5.0, 5.0)


class RunningMeanStdDictInPlace(nn.Module):
    def __init__(self, obs_space: gym.spaces.Dict, epsilon=_NORM_EPS, per_channel=False, norm_only=False):
        super(RunningMeanStdDictInPlace, self).__init__()
        self.obs_space: Final = obs_space
        self.running_mean_std = nn.ModuleDict({
            k: RunningMeanStdInPlace(space.shape, epsilon, per_channel, norm_only) for k, space in obs_space.spaces.items()
        })

    def forward(self, x: Dict[str, Tensor]) -> None:
        """Normalize in-place!"""
        for k, module in self.running_mean_std.items():
            module(x[k])


def running_mean_std_summaries(running_mean_std_module: Union[nn.Module, ScriptModule, RecursiveScriptModule]):
    m = running_mean_std_module
    res = dict()

    for name, buf in m.named_buffers():
        # converts MODULE_NAME.running_mean_std.obs.running_mean to obs.running_mean
        name = '_'.join(name.split('.')[-2:])

        if name.endswith('running_mean'):
            res[name] = buf.float().mean()
        elif name.endswith('running_var'):
            res[name.replace('_var', '_std')] = torch.sqrt(buf.float() + _NORM_EPS).mean()

    return res