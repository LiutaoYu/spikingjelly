import torch
import torch.nn as nn
import torch.nn.functional as F
import math

class NeuNorm(nn.Module):
    def __init__(self, in_channels, k=0.9):
        '''
        .. warning::
            可能是错误的实现。测试的结果表明，增加NeuNorm后的收敛速度和正确率反而下降了。



        :param in_channels: 输入数据的通道数
        :param k: 动量项系数

        Wu Y, Deng L, Li G, et al. Direct Training for Spiking Neural Networks: Faster, Larger, Better[C]. national conference on artificial intelligence, 2019, 33(01): 1311-1318.
        中提出的NeuNorm层。NeuNorm层必须放在二维卷积层后的脉冲神经元后，例如：

        Conv2d -> LIF -> NeuNorm

        要求输入的尺寸是[batch_size, in_channels, W, H]。

        in_channels是输入到NeuNorm层的通道数，也就是论文中的 :math:`F`。

        k是动量项系数，相当于论文中的 :math:`k_{\\tau 2}`。

        论文中的 :math:`\\frac{v}{F}` 会根据 :math:`k_{\\tau 2} + vF = 1` 自动算出。
        '''
        super().__init__()
        self.x = 0
        self.k0 = k
        self.k1 = (1 - self.k0) / in_channels**2
        self.w = nn.Parameter(torch.Tensor(in_channels, 1, 1))
        nn.init.kaiming_uniform_(self.w, a=math.sqrt(5))
    def forward(self, in_spikes: torch.Tensor):
        '''
        :param in_spikes: 来自上一个卷积层的输出脉冲，shape=[batch_size, in_channels, W, H]
        :return: 正则化后的脉冲，shape=[batch_size, in_channels, W, H]
        '''
        self.x = self.k0 * self.x + (self.k1 * in_spikes.sum(dim=1).unsqueeze(1))

        return in_spikes - self.w * self.x

    def reset(self):
        '''
        :return: None

        本层是一个有状态的层。此函数重置本层的状态变量。
        '''
        self.x = 0

class DCT(nn.Module):
    def __init__(self, kernel_size):
        '''
        :param kernel_size: 进行分块DCT变换的块大小

        将输入的shape=[*, W, H]的数据进行分块DCT变换的层，*表示任意额外添加的维度。变换只在最后2维进行，要求W和H都能\\
        整除kernel_size。

        DCT是AXAT的一种特例。
        '''
        super().__init__()
        self.kernel = torch.zeros(size=[kernel_size, kernel_size])
        for i in range(0, kernel_size):
            for j in range(kernel_size):
                if i == 0:
                    self.kernel[i][j] = math.sqrt(1 / kernel_size) * math.cos((j + 0.5) * math.pi * i / kernel_size)
                else:
                    self.kernel[i][j] = math.sqrt(2 / kernel_size) * math.cos((j + 0.5) * math.pi * i / kernel_size)

    def forward(self, x: torch.Tensor):
        '''
        :param x: shape=[*, W, H]，*表示任意额外添加的维度
        :return: 对x进行分块DCT变换后得到的tensor
        '''
        if self.kernel.device != x.device:
            self.kernel = self.kernel.to(x.device)
        x_shape = x.shape
        x = x.view(-1, x_shape[-2], x_shape[-1])
        ret = torch.zeros_like(x)
        for i in range(0, x_shape[-2], self.kernel.shape[0]):
            for j in range(0, x_shape[-1], self.kernel.shape[0]):
                ret[:, i:i + self.kernel.shape[0], j:j + self.kernel.shape[0]] \
                    = self.kernel.matmul(x[:, i:i + self.kernel.shape[0], j:j + self.kernel.shape[0]]).matmul(self.kernel.t())
        return ret.view(x_shape)

class AXAT(nn.Module):
    def __init__(self, in_features, out_features):
        '''
        :param in_features: 输入数据的最后2维的尺寸
        :param out_features: 输出数据的最后2维的尺寸

        对输入数据 :math:`X` 进行线性变换 :math:`AXA^{T}` 的操作。

        要求输入数据的shape=[*, in_features, in_features]，*表示任意额外添加的维度。

        将输入的数据看作是批量个shape=[in_features, in_features]的矩阵，而 :math:`A` 是shape=[out_features, in_features]的矩阵。
        '''
        super().__init__()
        self.A = nn.Parameter(torch.Tensor(out_features, in_features))
        nn.init.kaiming_uniform_(self.A, a=math.sqrt(5))


    def forward(self, x: torch.Tensor):
        '''
        :param x: 输入数据，shape=[*, in_features, in_features]，*表示任意额外添加的维度
        :return: 输出数据，shape=[*, out_features, out_features]
        '''
        x_shape = list(x.shape)
        x = x.view(-1, x_shape[-2], x_shape[-1])
        x = self.A.matmul(x).matmul(self.A.t())
        x_shape[-1] = x.shape[-1]
        x_shape[-2] = x.shape[-2]
        return x.view(x_shape)

class Dropout(nn.Module):
    def __init__(self, p=0.5):
        '''
        :param p: 设置为0的概率

        与torch.nn.Dropout的操作相同，但是在每一轮的仿真中，被设置成0的位置不会发生改变；直到下一轮运行，即网络调用reset()函\\
        数后，才会按照概率去重新决定，哪些位置被置0。

        torch.nn.Dropout在SNN中使用时，由于SNN需要运行一定的步长，每一步运行（t=0,1,...,T-1）时都会有不同的dropout，导致网络的结构\\
        实际上是在持续变化：例如可能出现t=0时刻，i到j的连接被断开，但t=1时刻，i到j的连接又被保持。

        在SNN中的dropout应该是，当前这一轮的运行中，t=0时若i到j的连接被断开，则之后t=1,2,...,T-1时刻，i到j的连接应该一直被\\
        断开；而到了下一轮运行时，重新按照概率去决定i到j的连接是否断开，因此重写了适用于SNN的Dropout。

        .. tip::
            从之前的实验结果可以看出，当使用LIF神经元，损失函数或分类结果被设置成时间上累计输出的值，torch.nn.Dropout几乎对SNN没有\\
            影响，即便dropout的概率被设置成高达0.9。可能是LIF神经元的积分行为，对某一个时刻输入的缺失并不敏感。
        '''
        super().__init__()
        assert 0 < p < 1
        self.mask = None
        self.p = p

    def extra_repr(self):
        return 'p={}'.format(
            self.p
        )

    def forward(self, x:torch.Tensor):
        '''
        :param x: shape=[*]的tensor
        :return: shape与x.shape相同的tensor
        '''
        if self.training:
            if self.mask is None:
                self.mask = F.dropout(torch.ones_like(x), self.p, training=True)
            return self.mask * x
        else:
            return x


    def reset(self):
        '''
        :return: None

        本层是一个有状态的层。此函数重置本层的状态变量。
        '''
        self.mask = None

class Dropout2d(nn.Module):
    def __init__(self, p=0.2):
        '''
        :param p: 设置为0的概率

        与torch.nn.Dropout2d的操作相同，但是在每一轮的仿真中，被设置成0的位置不会发生改变；直到下一轮运行，即网络调用reset()函\\
        数后，才会按照概率去重新决定，哪些位置被置0。

        '''
        super().__init__()
        assert 0 < p < 1
        self.mask = None
        self.p = p

    def extra_repr(self):
        return 'p={}'.format(
            self.p
        )

    def forward(self, x:torch.Tensor):
        '''
        :param x: shape=[N, C, W, H]的tensor
        :return: shape=[N, C, W, H]，与x.shape相同的tensor
        '''
        if self.training:
            if self.mask is None:
                self.mask = F.dropout2d(torch.ones_like(x), self.p, training=True)
            return self.mask * x
        else:
            return x

    def reset(self):
        '''
        :return: None

        本层是一个有状态的层。此函数重置本层的状态变量。
        '''
        self.mask = None

class LowPassSynapse(nn.Module):
    def __init__(self, tau=100.0, learnable=False):
        '''
        :param tau: 突触上电流衰减的时间常数
        :param learnable: 时间常数是否设置成可以学习的参数。当设置为可学习参数时，函数参数中的tau是该参数的初始值

        具有低通滤波性质的突触。突触的输出电流满足，当没有脉冲输入时，输出电流指数衰减：

        .. math::
            \\tau \\frac{\\mathrm{d} I(t)}{\\mathrm{d} t} = - I(t)

        当有新脉冲输入时，输出电流自增1：

        .. math::
            I(t) = I(t) + 1
        ..

        记输入脉冲为 :math:`S(t)`，则离散化后，统一的电流更新方程为：

        .. math::
            I(t) = I(t-1) - (1 - S(t)) \\frac{1}{\\tau} I(t-1) + S(t)

        这种突触能将输入脉冲进行“平滑”，简单的示例代码和输出结果：

        .. code-block:: python

            T = 50
            in_spikes = (torch.rand(size=[T]) >= 0.95).float()
            lp_syn = LowPassSynapse(tau=10.0)
            pyplot.subplot(2, 1, 1)
            pyplot.bar(torch.arange(0, T).tolist(), in_spikes, label='in spike')
            pyplot.xlabel('t')
            pyplot.ylabel('spike')
            pyplot.legend()

            out_i = []
            for i in range(T):
                out_i.append(lp_syn(in_spikes[i]))
            pyplot.subplot(2, 1, 2)
            pyplot.plot(out_i, label='out i')
            pyplot.xlabel('t')
            pyplot.ylabel('i')
            pyplot.legend()
            pyplot.show()

        .. image:: ./_static/API/LowPassSynapseFilter.png

        输出电流不仅取决于当前时刻的输入，还取决于之前的输入，使得该突触具有了一定的记忆能力。

        这种突触偶有使用，例如：

        Diehl P U, Cook M. Unsupervised learning of digit recognition using spike-timing-dependent plasticity.[J]. Frontiers in Computational Neuroscience, 2015: 99-99.

        Fang H, Shrestha A, Zhao Z, et al. Exploiting Neuron and Synapse Filter Dynamics in Spatial Temporal Learning of Deep Spiking Neural Network[J]. arXiv: Neural and Evolutionary Computing, 2020.

        另一种视角是将其视为一种输入为脉冲，并输出其电压的LIF神经元。并且该神经元的发放阈值为 :math:`+\infty` 。
        
        神经元最后累计的电压值一定程度上反映了该神经元在整个仿真过程中接收脉冲的数量，从而替代了传统的直接对输出脉冲计数（即发放频率）来表示神经元活跃程度的方法。因此通常用于最后一层，在以下文章中使用：

        Lee C, Sarwar S S, Panda P, et al. Enabling spike-based backpropagation for training deep neural network architectures[J]. Frontiers in Neuroscience, 2020, 14.
        '''
        super().__init__()
        if learnable:
            self.tau = nn.Parameter(torch.ones(size=[1]) / tau)
        else:
            self.tau = 1 / tau
        self.out_i = 0

    def extra_repr(self):
        return 'tau={}'.format(
            1 / self.tau
        )

    def forward(self, in_spikes: torch.Tensor):
        '''
        :param in_spikes: shape任意的输入脉冲
        :return: shape与in_spikes.shape相同的输出电流
        '''
        self.out_i = self.out_i - (1 - in_spikes) * self.out_i * self.tau + in_spikes
        return self.out_i

    def reset(self):
        '''
        :return: None

        本层是一个有状态的层。此函数重置本层的状态变量。将电流重置为0。
        '''
        self.out_i = 0

class ChannelsMaxPool(nn.Module):
    def __init__(self, pool: nn.MaxPool1d):
        '''
        :param pool: nn.Maxpool1d的池化层

        在通道所在的维度，第1维，进行池化的层。
        '''
        super().__init__()
        self.pool = pool

    def forward(self, x: torch.Tensor):
        '''
        :param x: shape=[batch_size, C_in, *]的tensor，C_in是输入数据的通道数，*表示任意维度
        :return: shape=[batch_size, C_out, *]的tensor，C_out是池化后的通道数
        '''
        x_shape = x.shape
        return self.pool(x.flatten(2).permute(0, 2, 1)).permute(0, 2, 1).view((x_shape[0], -1) + x_shape[2:])

class BatchNorm2d(nn.Module):
    def __init__(self, num_features, eps=1e-05, momentum=0.1, scaling=True, track_running_stats=True):
        '''
        :param num_features: 本层作用于 :math:`(N, C, H, W)` 的数据，``num_features`` 即为输入数据的通道数 :math:`C`

            :math:`C` from an expected input of size :math:`(N, C, H, W)`
        :param eps: 为了防止出现除以0造成数值不稳定而增加的小数，默认为1e-5

            a value added to the denominator for numerical stability.
            Default: 1e-5
        :param momentum: running_mean和running_var的动量项，若不需要动量项则设置成 ``None``，默认为0.1

            the value used for the running_mean and running_var
            computation. Can be set to ``None`` for cumulative moving average
            (i.e. simple average). Default: 0.1
        :param scaling: 是否使用缩放变换。若为 ``True`` 则会带有可学习的缩放变换参数，默认为 ``True``

            a boolean value that when set to ``True``, this module has
            learnable scaling parameters. Default: ``True``
        :param track_running_stats: 是否持续追踪整个训练过程中数据的均值和方差。若为 ``False`` 则只会使用每个batch内的均值和
            方差。默认为 ``True``

            a boolean value that when set to ``True``, this
            module tracks the running mean and variance, and when set to ``False``,
            this module does not track such statistics and always uses batch
            statistics in both training and eval modes. Default: ``True``

        当 ``scaling=False`` 时，与 ``torch.nn.BatchNorm2d(affline=False)`` 行为完全相同；当 ``scaling=True`` 时，缩放变换为：

        .. math::
            y = \\frac{x - \\mathrm{E}[x]}{ \\sqrt{\\mathrm{Var}[x] + \\epsilon}} * \\gamma

        When ``scaling=False``, this module is same with ``torch.nn.BatchNorm2d(affline=False)``. When ``scaling=True``,
        this module will do a scaling transform:

        .. math::
            y = \\frac{x - \\mathrm{E}[x]}{ \\sqrt{\\mathrm{Var}[x] + \\epsilon}} * \\gamma
        '''
        super().__init__()
        self.bn = nn.BatchNorm2d(num_features, eps, momentum, False, track_running_stats)
        if scaling:
            self.weight = nn.Parameter(torch.Tensor(num_features, 1, 1))
            nn.init.ones_(self.weight)
        else:
            self.weight = None

    def forward(self, x: torch.Tensor):
        if self.weight is None:
            return self.bn(x)
        else:
            return self.bn(x) * self.weight

    def __repr__(self):
        if self.weight is None:
            return 'layer.BatchNorm2d(\n' + self.bn.__repr__() + '\n)'
        else:
            return 'layer.BatchNorm2d(\n' + self.bn.__repr__() + '\nweight(num_features=' + str(self.weight.data.shape[0]) + ')\n)'