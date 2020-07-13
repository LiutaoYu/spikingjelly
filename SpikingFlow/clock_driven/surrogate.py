import torch
import torch.nn as nn
import torch.nn.functional as F

class bilinear_leaky_relu(torch.autograd.Function):
    @staticmethod
    def forward(ctx, x, a=1, b=0.01, c=0.5):
        if x.requires_grad:
            piecewise0 = (x < -c).float()
            piecewise2 = (x > c).float()
            piecewise1 = torch.ones_like(x) - piecewise0 - piecewise2
            ctx.save_for_backward(piecewise0 * b + piecewise1 * a + piecewise2 * b)
        return (x >= 0).float()

    @staticmethod
    def backward(ctx, grad_output):
        grad_x = None
        if ctx.needs_input_grad[0]:
            grad_x = grad_output * ctx.saved_tensors[0]
        return grad_x, None, None, None

class BilinearLeakyReLU(nn.Module):
    def __init__(self, a=1, b=0.01, c=0.5):
        '''
        :param a: -c <= x <= c 时反向传播的梯度
        :param b: x > c 或 x < -c 时反向传播的梯度
        :param c: 决定梯度区间的参数
        :return: 与输入相同shape的输出

        双线性的近似脉冲发放函数。梯度为

        .. math::
            g'(x) =
            \\begin{cases}
            a, & -c \\leq x \\leq c \\\\
            b, & x < -c ~or~ x > c
            \\end{cases}

        对应的原函数为

        .. math::
            g(x) =
            \\begin{cases}
            bx + bc - ac, & x < -c \\\\
            ax, & -c \\leq x \\leq c \\\\
            bx - bc + ac, & x > c \\\\
            \\end{cases}

        '''
        super().__init__()
        self.a = a
        self.b = b
        self.c = c
        self.f = bilinear_leaky_relu.apply
    def forward(self, x):
        return self.f(x, self.a, self.b, self.c)

class sigmoid(torch.autograd.Function):
    @staticmethod
    def forward(ctx, x, alpha):
        if x.requires_grad:
            alpha_x = x * alpha
            ctx.save_for_backward(alpha_x)
            ctx.alpha = alpha
        return (x >= 0).float()

    @staticmethod
    def backward(ctx, grad_output):
        grad_x = None
        if ctx.needs_input_grad[0]:
            alpha_x = ctx.saved_tensors[0]
            s_x = torch.sigmoid(alpha_x)
            grad_x = grad_output * s_x * (1 - s_x) * ctx.alpha
        return grad_x, None

class Sigmoid(nn.Module):
    def __init__(self, alpha=1.0):
        '''
        :param x: 输入数据
        :param alpha: 控制反向传播时梯度的平滑程度的参数
        :return: 与输入相同shape的输出

        反向传播时使用sigmoid的梯度的脉冲发放函数。反向传播为

        .. math::
            g'(x) = \\alpha * (1 - \\mathrm{sigmoid} (\\alpha x)) \\mathrm{sigmoid} (\\alpha x)

        对应的原函数为

        .. math::
            g(x) = \\mathrm{sigmoid}(\\alpha x) = \\frac{1}{1+e^{-\\alpha x}}
        '''
        super().__init__()
        self.alpha = alpha
        self.f = sigmoid.apply
    def forward(self, x):
        return self.f(x, self.alpha)

class sign_swish(torch.autograd.Function):
    @staticmethod
    def forward(ctx, x, beta=1.0):
        if x.requires_grad:
            beta_x = beta * x
            ctx.save_for_backward(beta_x)
            ctx.beta = beta
        return (x >= 0).float()

    @staticmethod
    def backward(ctx, grad_output):
        grad_x = None
        if ctx.needs_input_grad[0]:
            beta_x = ctx.saved_tensors[0]
            grad_x = ctx.beta * (2 - beta_x * torch.tanh(beta_x / 2)) / (1 + torch.cosh(beta_x)) \
                     * grad_output

        return grad_x, None

class SignSwish(nn.Module):
    def __init__(self, beta=5.0):
        '''
        :param x: 输入数据
        :param beta: 控制反向传播的参数
        :return: 与输入相同shape的输出

        Darabi, Sajad, et al. "BNN+: Improved binary network training." arXiv preprint arXiv:1812.11800 (2018).

        反向传播时使用swish的梯度的脉冲发放函数。反向传播为

        .. math::
            g'(x) = \\frac{\\beta (2 - \\beta x \\mathrm{tanh} \\frac{\\beta x}{2})}{1 + \\mathrm{cosh}(\\beta x)}

        对应的原函数为

        .. math::
            g(x) = 2 * \\mathrm{sigmoid}(\\beta x) * (1 + \\beta x (1 - \\mathrm{sigmoid}(\\beta x))) - 1
        '''
        super().__init__()
        self.beta = beta
        self.f = sign_swish.apply
    def forward(self, x):
        return self.f(x, self.beta)
