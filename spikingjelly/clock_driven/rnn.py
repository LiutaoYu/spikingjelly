import torch
import torch.nn as nn
import torch.nn.functional as F
from spikingjelly.clock_driven import surrogate, accelerating, layer
import math

class SpikingLSTMCell(nn.Module):
    def __init__(self, input_size: int, hidden_size: int, bias=True, v_threshold=1.0,
                 surrogate_function1=surrogate.Erf(), surrogate_function2=None):
        '''
        A `spiking` long short-term memory (LSTM) cell, which is firstly proposed in
        `Long Short-Term Memory Spiking Networks and Their Applications <https://arxiv.org/abs/2007.04779>`_.

        .. math::

            i &= \\Theta(W_{ii} x + b_{ii} + W_{hi} h + b_{hi}) \\\\
            f &= \\Theta(W_{if} x + b_{if} + W_{hf} h + b_{hf}) \\\\
            g &= \\Theta(W_{ig} x + b_{ig} + W_{hg} h + b_{hg}) \\\\
            o &= \\Theta(W_{io} x + b_{io} + W_{ho} h + b_{ho}) \\\\
            c' &= f * c + i * g \\\\
            h' &= o * c'

        where :math:`\\Theta` is the heaviside function, and :math:`*` is the Hadamard product.

        :param input_size: The number of expected features in the input ``x``
        :type input_size: int

        :param hidden_size: int
        :type hidden_size: The number of features in the hidden state ``h``

        :param bias: If ``False``, then the layer does not use bias weights ``b_ih`` and
            ``b_hh``. Default: ``True``
        :type bias: bool

        :param v_threshold: threshold voltage of neurons
        :type v_threshold: float

        :param surrogate_function1: surrogate function for replacing gradient of spiking functions during
            back-propagation, which is used for generating ``i``, ``f``, ``o``

        :param surrogate_function2: surrogate function for replacing gradient of spiking functions during
            back-propagation, which is used for generating ``g``. If ``None``, the surrogate function for generating ``g``
            will be set as ``surrogate_function1``. Default: ``None``

        .. admonition:: Note
            :class: note

            All the weights and biases are initialized from :math:`\\mathcal{U}(-\\sqrt{k}, \\sqrt{k})`
            where :math:`k = \\frac{1}{\\text{hidden_size}}`.

        Examples:

        .. code-block:: python

            T = 6
            batch_size = 2
            input_size = 3
            hidden_size = 4
            rnn = rnn.SpikingLSTMCell(input_size, hidden_size)
            input = torch.randn(T, batch_size, input_size) * 50
            h = torch.randn(batch_size, hidden_size)
            c = torch.randn(batch_size, hidden_size)

            output = []
            for t in range(T):
                h, c = rnn(input[t], (h, c))
                output.append(h)
            print(output)
        '''

        super().__init__()
        self.input_size = input_size
        self.hidden_size = hidden_size
        self.bias = bias
        self.v_threshold = v_threshold

        self.linear_ih = nn.Linear(input_size, 4 * hidden_size, bias=bias)
        self.linear_hh = nn.Linear(hidden_size, 4 * hidden_size, bias=bias)

        self.surrogate_function1 = surrogate_function1
        self.surrogate_function2 = surrogate_function2
        if self.surrogate_function2 is not None:
            assert self.surrogate_function1.spiking == self.surrogate_function2.spiking

        self.reset_parameters()



    def reset_parameters(self):
        sqrt_k = math.sqrt(1 / self.hidden_size)
        nn.init.uniform_(self.linear_ih.weight, -sqrt_k, sqrt_k)
        nn.init.uniform_(self.linear_hh.weight, -sqrt_k, sqrt_k)
        if self.bias is not None:
            nn.init.uniform_(self.linear_ih.bias, -sqrt_k, sqrt_k)
            nn.init.uniform_(self.linear_hh.bias, -sqrt_k, sqrt_k)

    def forward(self, x: torch.Tensor, hc=None):
        '''
        :param x: the input tensor with ``shape = [batch_size, input_size]``
        :type x: torch.Tensor

        :param hc: (h_0, c_0)
                h_0 : torch.Tensor
                    ``shape = [batch_size, hidden_size]``, tensor containing the initial hidden state for each element in the batch
                c_0 : torch.Tensor
                    ``shape = [batch_size, hidden_size]``, tensor containing the initial cell state for each element in the batch
                If (h_0, c_0) is not provided, both ``h_0`` and ``c_0`` default to zero
        :type hc: tuple or None
        :return: (h_1, c_1) :
                h_1 : torch.Tensor
                    ``shape = [batch_size, hidden_size]``, tensor containing the next hidden state for each element in the batch
                c_1 : torch.Tensor
                    ``shape = [batch_size, hidden_size]``, tensor containing the next cell state for each element in the batch
        :rtype: tuple
        '''
        if hc is None:
            h = torch.zeros(size=[x.shape[0], self.hidden_size], dtype=torch.float, device=x.device)
            c = torch.zeros_like(h)
        else:
            h = hc[0]
            c = hc[1]
        if self.surrogate_function2 is None:
            i, f, g, o = torch.split(self.surrogate_function1(self.linear_ih(x) + self.linear_hh(h) - self.v_threshold),
                                     self.hidden_size, dim=1)
        else:
            i, f, g, o = torch.split(self.linear_ih(x) + self.linear_hh(h) - self.v_threshold, self.hidden_size, dim=1)
            i = self.surrogate_function1(i)
            f = self.surrogate_function1(f)
            g = self.surrogate_function2(g)
            o = self.surrogate_function1(o)
        if self.surrogate_function2 is not None:
            assert self.surrogate_function1.spiking == self.surrogate_function2.spiking
        if self.surrogate_function1.spiking:
            # 可以使用针对脉冲的加速
            # c = f * c + i * g
            c = accelerating.mul(c, f) + accelerating.mul(i, g, True)
            # h = o * c
            h = accelerating.mul(c, o)
        else:
            c = c * f + i * g
            h = c * o
        return h, c

    def weight_ih(self):
        return self.linear_ih.weight

    def weight_hh(self):
        return self.linear_hh.weight

    def bias_ih(self):
        return self.linear_ih.bias

    def bias_hh(self):
        return self.linear_hh.bias

class SpikingLSTM(nn.Module):
    def __init__(self, input_size, hidden_size, num_layers, bias=True, dropout_p=0,
                 invariant_dropout_mask=False, bidirectional=False, v_threshold=1.0,
                 surrogate_function1=surrogate.Erf(), surrogate_function2=None):
        super().__init__()
        self.input_size = input_size
        self.hidden_size = hidden_size
        self.num_layers = num_layers
        self.dropout_p = dropout_p
        self.invariant_dropout_mask = invariant_dropout_mask
        self.bidirectional = bidirectional

        if self.bidirectional:
            raise NotImplementedError
        else:
            self.lstm_cells = []
            self.lstm_cells.append(SpikingLSTMCell(input_size, hidden_size, bias, v_threshold,
                                                   surrogate_function1, surrogate_function2))
            for i in range(num_layers - 1):
                self.lstm_cells.append(SpikingLSTMCell(hidden_size, hidden_size, bias, v_threshold,
                                                      surrogate_function1, surrogate_function2))
        self.lstm_cells = nn.Sequential(*self.lstm_cells)

    def forward(self, x: torch.Tensor, hc=None):
        # x.shape=[T, batch_size, input_size]
        T = x.shape[0]
        batch_size = x.shape[1]
        if self.bidirectional:
            raise NotImplementedError
        else:
            # 生成保存h和c的list
            h_list = torch.zeros(size=[self.num_layers, batch_size, self.hidden_size]).to(x)
            c_list = torch.zeros_like(h_list)
            # 初始的h c从输入获取
            if hc is not None:
                h_list = hc[0]
                c_list = hc[1]
            if self.training and self.dropout_p > 0 and self.invariant_dropout_mask:
                mask = F.dropout(torch.ones(size=[batch_size, self.hidden_size]), p=self.dropout_p, training=True, inplace=True).to(x)

            output = []
            for t in range(T):
                h_list[0], c_list[0] = self.lstm_cells[0](x[t], (h_list[0], c_list[0]))
                for i in range(1, self.num_layers):
                    h = h_list[i - 1]
                    if self.training and self.dropout_p > 0:
                        if self.invariant_dropout_mask:
                            h = h * mask
                        else:
                            h = F.dropout(h, p=self.dropout_p, training=True)
                    h_list[i], c_list[i] = self.lstm_cells[i](h, (h_list[i], c_list[i]))
                output.append(h_list[-1].unsqueeze(0))

            return torch.cat(output, dim=0), (h_list, c_list)






