'''
################################################################
# Layers - Residual-v2 blocks
# @ Modern Deep Network Toolkits for Tensorflow-Keras
# Yuchen Jin @ cainmagi@gmail.com
# Requirements: (Pay attention to version)
#   python 3.6+
#   tensorflow r1.13+
# Residual blocks and their deconvolutional versions.
#
# The first paper about residual block could be referred here:
# Bottleneck structure: Deep Residual Learning for Image 
# Recognition
#   https://arxiv.org/abs/1512.03385
# In this paper, the author proposes two structures for residual
# learning. The first one is one+"two conv." scheme, the other
# is "bottle neck" structure.
# In this module, we would implement the bottle neck structure
# and popularize it into deeper case. For example, a N+2 depth
# bottle neck residual block could be represented as:
#   Conv x1 -> Conv xM -> Conv xM -> ... -> Conv x1
#              \---- N Conv ----/
# The stride should be implemented on the first Conv and the 
# channel change should be implemented on the last Conv.
#
# The author also proposes a revised version after involving
# the batch normalization scheme. The convolutional layer
# inside the residual block should be implemented as `Norm ->`
# `Actv -> Conv` rather than `Conv -> Norm -> Actv`
# according such a paper:
# Sublayer order: Identity Mappings in Deep Residual Networks
#   https://arxiv.org/abs/1603.05027v3
# In this module, the order of the sub-layers of modern conv.
#
# In the third paper, the structure of the residual block moves 
# onto the next stage, ResNeXt. 
# ResNeXt means the next dimension of residual block. Similar
# to group normalization which is between the batch normaliza-
# tion and instance normalization, the convolutional part in 
# ResNeXt could be viewed as an intermediate structure between
# the trivial convolutional layer and separable convolutional
# layer. The core idea of this structure is dividing the chan-
# nels into several groups and applying convolution in each
# group. If each group only has one channel, the structure fa-
# lls back to separable convolution; If there is only one gro-
# up, the structure falls back to trivial convolution.
# The paper about ResNeXt could be referred here:
# Aggregated Residual Transformations for Deep Neural Networks
#   https://arxiv.org/abs/1611.05431
#
# layers has been modified according to the residual-v2 theory.
# Version: 0.42 # 2019/6/27
# Comments:
#   Switch back to the version where projection layers have
#   normalizations.
# Version: 0.41 # 2019/6/20
# Comments:
#   Remove the normalization for the projection convolutional
#   branch of the residual block and the ResNeXt block.
# Version: 0.40 # 2019/6/12
# Comments:
#   1. Fix the bug for calculating spatial dropout.
#   2. Enable ResNeXt to work with dropout.
#   3. Strengthen the compatibility.
# Version: 0.37-b # 2019/6/11
# Comments:
#   Test to check the performance of applying droupout inside
#   residual layer.
# Version: 0.37 # 2019/6/7
# Comments:
#   Enable ResNeXt to estimate the latent group and local 
#   filter number.
# Version: 0.35 # 2019/6/6
# Comments:
#   Fix memory consumption problem of ResNeXt layers by using
#   group convolution.
# Version: 0.30 # 2019/6/5
# Comments:
#   Adding ResNeXt (residual-v3) layers to this module.
# Version: 0.20 # 2019/5/31
# Comments:
#   Finish this submodule and fix the bugs caused by parameter
#   searching scheme.
# Version: 0.10 # 2019/3/23
# Comments:
#   Create this submodule.
################################################################
'''

from tensorflow.python.framework import tensor_shape
from tensorflow.python.keras import activations
from tensorflow.python.keras import backend as K
from tensorflow.python.keras import constraints
from tensorflow.python.keras import initializers
from tensorflow.python.keras import regularizers
from tensorflow.python.keras.utils import conv_utils
from tensorflow.python.keras.engine.base_layer import Layer

from tensorflow.python.keras.layers.convolutional import Conv, UpSampling1D, UpSampling2D, UpSampling3D, ZeroPadding1D, ZeroPadding2D, ZeroPadding3D, Cropping1D, Cropping2D, Cropping3D
from tensorflow.python.keras.layers.merge import Add, Concatenate
from .unit import NACUnit
from .conv import _AConv
from .dropout import return_dropout

from .. import compat
if compat.COMPATIBLE_MODE['1.12']:
    from tensorflow.python.keras.engine.base_layer import InputSpec
else:
    from tensorflow.python.keras.engine.input_spec import InputSpec

from functools import reduce
from math import sqrt
_check_dl_func = lambda a: all(ai==1 for ai in a)
_cal_quad_root = lambda a, b, c: (sqrt(b**2 - 4*a*c) - b)/(2*a)
def _get_prod(x):
    try:
        return reduce(lambda a,b:a*b, x)
    except TypeError:
        return x

class _Residual(Layer):
    """Modern residual layer.
    Abstract nD residual layer (private, used as implementation base).
    `_Residual` implements the operation:
        `output = AConv(input) + Conv(Actv(Norm( Conv(Actv(Norm( ... ))) )))`
    In some cases, the first term may not need to be convoluted.
    Such a structure is mainly brought from:
        Bottleneck structure: Deep Residual Learning for Image Recognition
            https://arxiv.org/abs/1512.03385
        Sublayer order: Identity Mappings in Deep Residual Networks
            https://arxiv.org/abs/1603.05027v3
    Experiments show that the aforementioned implementation may be the optimal
    design for residual block. We popularize the residual block into the case that
    enables any depth.
    Arguments for residual block:
        rank: An integer, the rank of the convolution, e.g. "2" for 2D convolution.
        depth: An integer, indicates the repentance of convolutional blocks.
        ofilters: Integer, the dimensionality of the output space (i.e. the number
            of filters of output).
        lfilters: Integer, the dimensionality of the lattent space (i.e. the number
            of filters in the convolution branch).
    Arguments for convolution:
        kernel_size: An integer or tuple/list of n integers, specifying the
            length of the convolution window.
        strides: An integer or tuple/list of n integers,
            specifying the stride length of the convolution.
            Specifying any stride value != 1 is incompatible with specifying
            any `dilation_rate` value != 1.
        data_format: A string, one of `channels_last` (default) or `channels_first`.
            The ordering of the dimensions in the inputs.
            `channels_last` corresponds to inputs with shape
            `(batch, ..., channels)` while `channels_first` corresponds to
            inputs with shape `(batch, channels, ...)`.
        dilation_rate: An integer or tuple/list of n integers, specifying
            the dilation rate to use for dilated convolution.
            Currently, specifying any `dilation_rate` value != 1 is
            incompatible with specifying any `strides` value != 1.
        kernel_initializer: An initializer for the convolution kernel.
        kernel_regularizer: Optional regularizer for the convolution kernel.
        kernel_constraint: Optional projection function to be applied to the
            kernel after being updated by an `Optimizer` (e.g. used to implement
            norm constraints or value constraints for layer weights). The function
            must take as input the unprojected variable and must return the
            projected variable (which must have the same shape). Constraints are
            not safe to use when doing asynchronous distributed training.
        trainable: Boolean, if `True` also add variables to the graph collection
            `GraphKeys.TRAINABLE_VARIABLES` (see `tf.Variable`).
        name: A string, the name of the layer.
    Arguments for normalization:
        normalization: The normalization type, which could be
            (1) None:  do not use normalization and do not add biases.
            (2) bias:  apply biases instead of using normalization.
            (3) batch: use batch normalization.
            (4) inst : use instance normalization.
            (5) group: use group normalization.
            If using (2), the initializer, regularizer and constraint for
            beta would be applied to the bias of convolution.
        beta_initializer: Initializer for the beta weight.
        gamma_initializer: Initializer for the gamma weight.
        beta_regularizer: Optional regularizer for the beta weight.
        gamma_regularizer: Optional regularizer for the gamma weight.
        beta_constraint: Optional constraint for the beta weight.
        gamma_constraint: Optional constraint for the gamma weight.
        groups (only for group normalization): Integer, the number of 
            groups for Group Normalization.
            Can be in the range [1, N] where N is the input dimension.
            The input dimension must be divisible by the number of groups.
    Arguments for dropout: (drop out would be only applied on the entrance
                            of conv. branch.)
        dropout: The dropout type, which could be
            (1) None:    do not use dropout.
            (2) plain:   use tf.keras.layers.Dropout.
            (3) add:     use scale-invariant addictive noise.
                         (mdnt.layers.InstanceGaussianNoise)
            (4) mul:     use multiplicative noise.
                         (tf.keras.layers.GaussianDropout)
            (5) alpha:   use alpha dropout. (tf.keras.layers.AlphaDropout)
            (6) spatial: use spatial dropout (tf.keras.layers.SpatialDropout)
        dropout_rate: The drop probability. In `add` mode, it is used as
            maximal std. To learn more, please see the docstrings of each
            method.
    Arguments for activation:
        activation: Activation function to use
            (see [activations](../activations.md)).
            If you don't specify anything, no activation is applied
            (ie. "linear" activation: `a(x) = x`).
        activity_config: keywords for the parameters of activation
            function (only for lrelu).
    Arguments (others):
        activity_regularizer: Regularizer function applied to
            the output of the layer (its "activation").
            (see [regularizer](../regularizers.md)).
    """

    def __init__(self, rank,
                 depth, ofilters,
                 kernel_size,
                 lfilters=None,
                 strides=1,
                 data_format=None,
                 dilation_rate=1,
                 kernel_initializer='glorot_uniform',
                 kernel_regularizer=None,
                 kernel_constraint=None,
                 normalization='inst',
                 beta_initializer='zeros',
                 gamma_initializer='ones',
                 beta_regularizer=None,
                 gamma_regularizer=None,
                 beta_constraint=None,
                 gamma_constraint=None,
                 groups=32,
                 dropout=None,
                 dropout_rate=0.3,
                 activation=None,
                 activity_config=None,
                 activity_regularizer=None,
                 trainable=True,
                 name=None,
                 _high_activation=None,
                 **kwargs):
        if 'input_shape' not in kwargs and 'input_dim' in kwargs:
          kwargs['input_shape'] = (kwargs.pop('input_dim'),)

        super(_Residual, self).__init__(trainable=trainable, name=name, **kwargs)
        # Inherit from keras.layers._Conv
        self.rank = rank
        self.depth = depth - 2
        self.ofilters = ofilters
        self.lfilters = lfilters
        if self.depth < 1:
            raise ValueError('The depth of the residual block should be >= 3.')
        self.kernel_size = conv_utils.normalize_tuple(
            kernel_size, rank, 'kernel_size')
        self.strides = conv_utils.normalize_tuple(strides, rank, 'strides')
        self.data_format = conv_utils.normalize_data_format(data_format)
        self.dilation_rate = conv_utils.normalize_tuple(
            dilation_rate, rank, 'dilation_rate')
        if (not _check_dl_func(self.dilation_rate)) and (not _check_dl_func(self.strides)):
            raise ValueError('Does not support dilation_rate when strides > 1.')
        self.kernel_initializer = initializers.get(kernel_initializer)
        self.kernel_regularizer = regularizers.get(kernel_regularizer)
        self.kernel_constraint = constraints.get(kernel_constraint)
        self.activity_regularizer = regularizers.get(activity_regularizer)
        # Inherit from mdnt.layers.normalize
        self.normalization = normalization
        if isinstance(normalization, str) and normalization in ('batch', 'inst', 'group'):
            self.gamma_initializer = initializers.get(gamma_initializer)
            self.gamma_regularizer = regularizers.get(gamma_regularizer)
            self.gamma_constraint = constraints.get(gamma_constraint)
        else:
            self.gamma_initializer = None
            self.gamma_regularizer = None
            self.gamma_constraint = None
        self.beta_initializer = initializers.get(beta_initializer)
        self.beta_regularizer = regularizers.get(beta_regularizer)
        self.beta_constraint = constraints.get(beta_constraint)
        self.groups = groups
        # Inherit from mdnt.layers.dropout
        self.dropout = dropout
        self.dropout_rate = dropout_rate
        # Inherit from keras.engine.Layer
        if _high_activation is not None:
            activation = _high_activation
        self.high_activation = _high_activation
        if isinstance(activation, str) and (activation.casefold() in ('prelu','lrelu')):
            self.activation = activations.get(None)
            self.high_activation = activation.casefold()
            self.activity_config = activity_config # dictionary passed to activation
        elif activation is not None:
            self.activation = activations.get(activation)
            self.activity_config = None
        self.sub_activity_regularizer=regularizers.get(activity_regularizer)

        # Reserve for build()
        self.channelIn = None
        
        self.trainable = trainable
        self.input_spec = InputSpec(ndim=self.rank + 2)

    def build(self, input_shape):
        input_shape = tensor_shape.TensorShape(input_shape)
        input_shape = input_shape.with_rank_at_least(self.rank + 2)
        if self.data_format == 'channels_first':
            channel_axis = 1
        else:
            channel_axis = -1
        if input_shape.dims[channel_axis].value is None:
            raise ValueError('The channel dimension of the inputs should be defined. Found `None`.')
        self.channelIn = int(input_shape[channel_axis])
        if self.lfilters is None:
            self.lfilters = max( 1, self.channelIn // 2 )
        last_use_bias = True
        if _check_dl_func(self.strides) and self.ofilters == self.channelIn:
            self.layer_branch_left = None
            left_shape = input_shape
        else:
            last_use_bias = False
            self.layer_branch_left = _AConv(rank = self.rank,
                          filters = self.ofilters,
                          kernel_size = 1,
                          strides = self.strides,
                          padding = 'same',
                          data_format = self.data_format,
                          dilation_rate = 1,
                          kernel_initializer=self.kernel_initializer,
                          kernel_regularizer=self.kernel_regularizer,
                          kernel_constraint=self.kernel_constraint,
                          normalization=self.normalization,
                          beta_initializer=self.beta_initializer,
                          gamma_initializer=self.gamma_initializer,
                          beta_regularizer=self.beta_regularizer,
                          gamma_regularizer=self.gamma_regularizer,
                          beta_constraint=self.beta_constraint,
                          gamma_constraint=self.gamma_constraint,
                          groups=self.groups,
                          activation=None,
                          activity_config=None,
                          activity_regularizer=None,
                          _high_activation=None,
                          trainable=self.trainable)
            self.layer_branch_left.build(input_shape)
            compat.collect_properties(self, self.layer_branch_left) # for compatibility
            left_shape = self.layer_branch_left.compute_output_shape(input_shape)
        # Right branch, with dropout
        self.layer_dropout = return_dropout(self.dropout, self.dropout_rate, axis=channel_axis, rank=self.rank)
        if self.layer_dropout is not None:
            self.layer_dropout.build(input_shape)
            right_shape = self.layer_dropout.compute_output_shape(input_shape)
        else:
            right_shape = input_shape
        self.layer_first = NACUnit(rank = self.rank,
                          filters = self.lfilters,
                          kernel_size = 1,
                          strides = self.strides,
                          padding = 'same',
                          data_format = self.data_format,
                          dilation_rate = 1,
                          kernel_initializer=self.kernel_initializer,
                          kernel_regularizer=self.kernel_regularizer,
                          kernel_constraint=self.kernel_constraint,
                          normalization=self.normalization,
                          beta_initializer=self.beta_initializer,
                          gamma_initializer=self.gamma_initializer,
                          beta_regularizer=self.beta_regularizer,
                          gamma_regularizer=self.gamma_regularizer,
                          beta_constraint=self.beta_constraint,
                          gamma_constraint=self.gamma_constraint,
                          groups=self.groups,
                          activation=self.activation,
                          activity_config=self.activity_config,
                          activity_regularizer=self.sub_activity_regularizer,
                          _high_activation=self.high_activation,
                          trainable=self.trainable)
        self.layer_first.build(right_shape)
        compat.collect_properties(self, self.layer_first) # for compatibility
        right_shape = self.layer_first.compute_output_shape(right_shape)
        # Repeat blocks by depth number
        for i in range(self.depth):
            if i == 0:
                sub_dilation_rate = self.dilation_rate
            else:
                sub_dilation_rate = 1
            layer_middle = NACUnit(rank = self.rank,
                          filters = self.lfilters,
                          kernel_size = self.kernel_size,
                          strides = 1,
                          padding = 'same',
                          data_format = self.data_format,
                          dilation_rate = sub_dilation_rate,
                          kernel_initializer=self.kernel_initializer,
                          kernel_regularizer=self.kernel_regularizer,
                          kernel_constraint=self.kernel_constraint,
                          normalization=self.normalization,
                          beta_initializer=self.beta_initializer,
                          gamma_initializer=self.gamma_initializer,
                          beta_regularizer=self.beta_regularizer,
                          gamma_regularizer=self.gamma_regularizer,
                          beta_constraint=self.beta_constraint,
                          gamma_constraint=self.gamma_constraint,
                          groups=self.groups,
                          activation=self.activation,
                          activity_config=self.activity_config,
                          activity_regularizer=self.sub_activity_regularizer,
                          _high_activation=self.high_activation,
                          trainable=self.trainable)
            layer_middle.build(right_shape)
            compat.collect_properties(self, layer_middle) # for compatibility
            right_shape = layer_middle.compute_output_shape(right_shape)
            setattr(self, 'layer_middle_{0:02d}'.format(i), layer_middle)
        self.layer_last = NACUnit(rank = self.rank,
                          filters = self.ofilters,
                          kernel_size = 1,
                          strides = 1,
                          padding = 'same',
                          data_format = self.data_format,
                          dilation_rate = 1,
                          kernel_initializer=self.kernel_initializer,
                          kernel_regularizer=self.kernel_regularizer,
                          kernel_constraint=self.kernel_constraint,
                          normalization=self.normalization,
                          beta_initializer=self.beta_initializer,
                          gamma_initializer=self.gamma_initializer,
                          beta_regularizer=self.beta_regularizer,
                          gamma_regularizer=self.gamma_regularizer,
                          beta_constraint=self.beta_constraint,
                          gamma_constraint=self.gamma_constraint,
                          groups=self.groups,
                          activation=self.activation,
                          activity_config=self.activity_config,
                          activity_regularizer=self.sub_activity_regularizer,
                          _high_activation=self.high_activation,
                          _use_bias=last_use_bias,
                          trainable=self.trainable)
        self.layer_last.build(right_shape)
        compat.collect_properties(self, self.layer_last) # for compatibility
        right_shape = self.layer_last.compute_output_shape(right_shape)
        self.layer_merge = Add()
        self.layer_merge.build([left_shape, right_shape])
        super(_Residual, self).build(input_shape)

    def call(self, inputs):
        if self.layer_branch_left is not None:
            branch_left = self.layer_branch_left(inputs)
        else:
            branch_left = inputs
        if self.layer_dropout is not None:
            branch_right = self.layer_dropout(inputs)
        else:
            branch_right = inputs
        branch_right = self.layer_first(branch_right)
        for i in range(self.depth):
            layer_middle = getattr(self, 'layer_middle_{0:02d}'.format(i))
            branch_right = layer_middle(branch_right)
        branch_right = self.layer_last(branch_right)
        outputs = self.layer_merge([branch_left, branch_right])
        return outputs

    def compute_output_shape(self, input_shape):
        if self.layer_branch_left is not None:
            branch_left_shape = self.layer_branch_left.compute_output_shape(input_shape)
        else:
            branch_left_shape = input_shape
        if self.layer_dropout is not None:
            branch_right_shape = self.layer_dropout.compute_output_shape(input_shape)
        else:
            branch_right_shape = input_shape
        branch_right_shape = self.layer_first.compute_output_shape(branch_right_shape)
        for i in range(self.depth):
            layer_middle = getattr(self, 'layer_middle_{0:02d}'.format(i))
            branch_right_shape = layer_middle.compute_output_shape(branch_right_shape)
        branch_right_shape = self.layer_last.compute_output_shape(branch_right_shape)
        next_shape = self.layer_merge.compute_output_shape([branch_left_shape, branch_right_shape])
        return next_shape
    
    def get_config(self):
        config = {
            'depth': self.depth + 2,
            'ofilters': self.ofilters,
            'lfilters': self.lfilters,
            'kernel_size': self.kernel_size,
            'strides': self.strides,
            'data_format': self.data_format,
            'dilation_rate': self.dilation_rate,
            'kernel_initializer': initializers.serialize(self.kernel_initializer),
            'kernel_regularizer': regularizers.serialize(self.kernel_regularizer),
            'kernel_constraint': constraints.serialize(self.kernel_constraint),
            'normalization': self.normalization,
            'beta_initializer': initializers.serialize(self.beta_initializer),
            'gamma_initializer': initializers.serialize(self.gamma_initializer),
            'beta_regularizer': regularizers.serialize(self.beta_regularizer),
            'gamma_regularizer': regularizers.serialize(self.gamma_regularizer),
            'beta_constraint': constraints.serialize(self.beta_constraint),
            'gamma_constraint': constraints.serialize(self.gamma_constraint),
            'groups': self.groups,
            'dropout': self.dropout,
            'dropout_rate': self.dropout_rate,
            'activation': activations.serialize(self.activation),
            'activity_config': self.activity_config,
            'activity_regularizer': regularizers.serialize(self.sub_activity_regularizer),
            '_high_activation': self.high_activation
        }
        base_config = super(_Residual, self).get_config()
        return dict(list(base_config.items()) + list(config.items()))
        
class Residual1D(_Residual):
    """1D residual layer.
    `Residual1D` implements the operation:
        `output = AConv1D(input) + Conv1D(Actv(Norm( Conv1D(Actv(Norm( ... ))) )))`
    In some cases, the first term may not need to be convoluted.
    This residual block supports users to use any depth. If depth=3, it is the same
    as bottleneck design. Deeper block means more convolutional layers.
    According to relative papers, the structure of this block has been optimized.
    Arguments for residual block:
        depth: An integer, indicates the repentance of convolutional blocks.
        ofilters: Integer, the dimensionality of the output space (i.e. the number
            of filters of output).
        lfilters: Integer, the dimensionality of the lattent space (i.e. the number
            of filters in the convolution branch).
    Arguments for convolution:
        kernel_size: An integer or tuple/list of a single integer,
            specifying the length of the 1D convolution window.
        strides: An integer or tuple/list of a single integer,
            specifying the stride length of the convolution.
            Specifying any stride value != 1 is incompatible with specifying
            any `dilation_rate` value != 1.
        data_format: A string,
            one of `channels_last` (default) or `channels_first`.
        dilation_rate: an integer or tuple/list of a single integer, specifying
            the dilation rate to use for dilated convolution.
            Currently, specifying any `dilation_rate` value != 1 is
            incompatible with specifying any `strides` value != 1.
        kernel_initializer: Initializer for the `kernel` weights matrix.
        kernel_regularizer: Regularizer function applied to
            the `kernel` weights matrix.
        kernel_constraint: Constraint function applied to the kernel matrix.
    Arguments for normalization:
        normalization: The normalization type, which could be
            (1) None:  do not use normalization and do not add biases.
            (2) bias:  apply biases instead of using normalization.
            (3) batch: use batch normalization.
            (4) inst : use instance normalization.
            (5) group: use group normalization.
            If using (2), the initializer, regularizer and constraint for
            beta would be applied to the bias of convolution.
        beta_initializer: Initializer for the beta weight.
        gamma_initializer: Initializer for the gamma weight.
        beta_regularizer: Optional regularizer for the beta weight.
        gamma_regularizer: Optional regularizer for the gamma weight.
        beta_constraint: Optional constraint for the beta weight.
        gamma_constraint: Optional constraint for the gamma weight.
        groups (only for group normalization): Integer, the number of 
            groups for Group Normalization.
            Can be in the range [1, N] where N is the input dimension.
            The input dimension must be divisible by the number of groups.
    Arguments for dropout: (drop out would be only applied on the entrance
                            of conv. branch.)
        dropout: The dropout type, which could be
            (1) None:    do not use dropout.
            (2) plain:   use tf.keras.layers.Dropout.
            (3) add:     use scale-invariant addictive noise.
                         (mdnt.layers.InstanceGaussianNoise)
            (4) mul:     use multiplicative noise.
                         (tf.keras.layers.GaussianDropout)
            (5) alpha:   use alpha dropout. (tf.keras.layers.AlphaDropout)
            (6) spatial: use spatial dropout (tf.keras.layers.SpatialDropout)
        dropout_rate: The drop probability. In `add` mode, it is used as
            maximal std. To learn more, please see the docstrings of each
            method.
    Arguments for activation:
        activation: Activation function to use
            (see [activations](../activations.md)).
            If you don't specify anything, no activation is applied
            (ie. "linear" activation: `a(x) = x`).
        activity_config: keywords for the parameters of activation
            function (only for lrelu).
    Arguments (others):
        activity_regularizer: Regularizer function applied to
            the output of the layer (its "activation").
            (see [regularizer](../regularizers.md)).
    Input shape:
        3D tensor with shape: `(batch_size, steps, input_dim)`
    Output shape:
        3D tensor with shape: `(batch_size, new_steps, filters)`
        `steps` value might have changed due to padding or strides.
    """

    def __init__(self,
               ofilters,
               kernel_size,
               lfilters=None,
               depth=3,
               strides=1,
               data_format='channels_last',
               dilation_rate=1,
               kernel_initializer='glorot_uniform',
               kernel_regularizer=None,
               kernel_constraint=None,
               normalization='inst',
               beta_initializer='zeros',
               gamma_initializer='ones',
               beta_regularizer=None,
               gamma_regularizer=None,
               beta_constraint=None,
               gamma_constraint=None,
               groups=32,
               dropout=None,
               dropout_rate=0.3,
               activation=None,
               activity_config=None,
               activity_regularizer=None,
               **kwargs):
        super(Residual1D, self).__init__(
            rank=1, depth=depth, ofilters=ofilters,
            kernel_size=kernel_size,
            lfilters=lfilters,
            strides=strides,
            data_format=data_format,
            dilation_rate=dilation_rate,
            kernel_initializer=initializers.get(kernel_initializer),
            kernel_regularizer=regularizers.get(kernel_regularizer),
            kernel_constraint=constraints.get(kernel_constraint),
            normalization=normalization,
            beta_initializer=initializers.get(beta_initializer),
            gamma_initializer=initializers.get(gamma_initializer),
            beta_regularizer=regularizers.get(beta_regularizer),
            gamma_regularizer=regularizers.get(gamma_regularizer),
            beta_constraint=constraints.get(beta_constraint),
            gamma_constraint=constraints.get(gamma_constraint),
            groups=groups,
            dropout=dropout,
            dropout_rate=dropout_rate,
            activation=activation,
            activity_config=activity_config,
            activity_regularizer=regularizers.get(activity_regularizer),
            **kwargs)
        
class Residual2D(_Residual):
    """2D residual layer (e.g. spatial convolution over images).
    `Residual2D` implements the operation:
        `output = AConv2D(input) + Conv2D(Actv(Norm( Conv2D(Actv(Norm( ... ))) )))`
    In some cases, the first term may not need to be convoluted.
    This residual block supports users to use any depth. If depth=3, it is the same
    as bottleneck design. Deeper block means more convolutional layers.
    According to relative papers, the structure of this block has been optimized.
    Arguments for residual block:
        depth: An integer, indicates the repentance of convolutional blocks.
        ofilters: Integer, the dimensionality of the output space (i.e. the number
            of filters of output).
        lfilters: Integer, the dimensionality of the lattent space (i.e. the number
            of filters in the convolution branch).
    Arguments for convolution:
        kernel_size: An integer or tuple/list of 2 integers, specifying the
            height and width of the 2D convolution window.
            Can be a single integer to specify the same value for
            all spatial dimensions.
        strides: An integer or tuple/list of 2 integers,
            specifying the strides of the convolution along the height and width.
            Can be a single integer to specify the same value for
            all spatial dimensions.
            Specifying any stride value != 1 is incompatible with specifying
            any `dilation_rate` value != 1.
        data_format: A string,
            one of `channels_last` (default) or `channels_first`.
            The ordering of the dimensions in the inputs.
            `channels_last` corresponds to inputs with shape
            `(batch, height, width, channels)` while `channels_first`
            corresponds to inputs with shape
            `(batch, channels, height, width)`.
            It defaults to the `image_data_format` value found in your
            Keras config file at `~/.keras/keras.json`.
            If you never set it, then it will be "channels_last".
        dilation_rate: an integer or tuple/list of 2 integers, specifying
            the dilation rate to use for dilated convolution.
            Can be a single integer to specify the same value for
            all spatial dimensions.
            Currently, specifying any `dilation_rate` value != 1 is
            incompatible with specifying any stride value != 1.
        kernel_initializer: Initializer for the `kernel` weights matrix.
        kernel_regularizer: Regularizer function applied to
            the `kernel` weights matrix.
        kernel_constraint: Constraint function applied to the kernel matrix.
    Arguments for normalization:
        normalization: The normalization type, which could be
            (1) None:  do not use normalization and do not add biases.
            (2) bias:  apply biases instead of using normalization.
            (3) batch: use batch normalization.
            (4) inst : use instance normalization.
            (5) group: use group normalization.
            If using (2), the initializer, regularizer and constraint for
            beta would be applied to the bias of convolution.
        beta_initializer: Initializer for the beta weight.
        gamma_initializer: Initializer for the gamma weight.
        beta_regularizer: Optional regularizer for the beta weight.
        gamma_regularizer: Optional regularizer for the gamma weight.
        beta_constraint: Optional constraint for the beta weight.
        gamma_constraint: Optional constraint for the gamma weight.
        groups (only for group normalization): Integer, the number of 
            groups for Group Normalization.
            Can be in the range [1, N] where N is the input dimension.
            The input dimension must be divisible by the number of groups.
    Arguments for dropout: (drop out would be only applied on the entrance
                            of conv. branch.)
        dropout: The dropout type, which could be
            (1) None:    do not use dropout.
            (2) plain:   use tf.keras.layers.Dropout.
            (3) add:     use scale-invariant addictive noise.
                         (mdnt.layers.InstanceGaussianNoise)
            (4) mul:     use multiplicative noise.
                         (tf.keras.layers.GaussianDropout)
            (5) alpha:   use alpha dropout. (tf.keras.layers.AlphaDropout)
            (6) spatial: use spatial dropout (tf.keras.layers.SpatialDropout)
        dropout_rate: The drop probability. In `add` mode, it is used as
            maximal std. To learn more, please see the docstrings of each
            method.
    Arguments for activation:
        activation: Activation function to use
            (see [activations](../activations.md)).
            If you don't specify anything, no activation is applied
            (ie. "linear" activation: `a(x) = x`).
        activity_config: keywords for the parameters of activation
            function (only for lrelu).
    Arguments (others):
        activity_regularizer: Regularizer function applied to
            the output of the layer (its "activation").
            (see [regularizer](../regularizers.md)).
    Input shape:
        4D tensor with shape:
        `(samples, channels, rows, cols)` if data_format='channels_first'
        or 4D tensor with shape:
        `(samples, rows, cols, channels)` if data_format='channels_last'.
    Output shape:
        4D tensor with shape:
        `(samples, filters, new_rows, new_cols)` if data_format='channels_first'
        or 4D tensor with shape:
        `(samples, new_rows, new_cols, filters)` if data_format='channels_last'.
        `rows` and `cols` values might have changed due to padding.
    """

    def __init__(self,
               ofilters,
               kernel_size,
               lfilters=None,
               depth=3,
               strides=(1, 1),
               data_format='channels_last',
               dilation_rate=(1, 1),
               kernel_initializer='glorot_uniform',
               kernel_regularizer=None,
               kernel_constraint=None,
               normalization='inst',
               beta_initializer='zeros',
               gamma_initializer='ones',
               beta_regularizer=None,
               gamma_regularizer=None,
               beta_constraint=None,
               gamma_constraint=None,
               groups=32,
               dropout=None,
               dropout_rate=0.3,
               activation=None,
               activity_config=None,
               activity_regularizer=None,
               **kwargs):
        super(Residual2D, self).__init__(
            rank=2, depth=depth, ofilters=ofilters,
            kernel_size=kernel_size,
            lfilters=lfilters,
            strides=strides,
            data_format=data_format,
            dilation_rate=dilation_rate,
            kernel_initializer=initializers.get(kernel_initializer),
            kernel_regularizer=regularizers.get(kernel_regularizer),
            kernel_constraint=constraints.get(kernel_constraint),
            normalization=normalization,
            beta_initializer=initializers.get(beta_initializer),
            gamma_initializer=initializers.get(gamma_initializer),
            beta_regularizer=regularizers.get(beta_regularizer),
            gamma_regularizer=regularizers.get(gamma_regularizer),
            beta_constraint=constraints.get(beta_constraint),
            gamma_constraint=constraints.get(gamma_constraint),
            groups=groups,
            dropout=dropout,
            dropout_rate=dropout_rate,
            activation=activation,
            activity_config=activity_config,
            activity_regularizer=regularizers.get(activity_regularizer),
            **kwargs)
        
class Residual3D(_Residual):
    """3D residual layer (e.g. spatial convolution over volumes).
    `Residual3D` implements the operation:
        `output = AConv3D(input) + Conv3D(Actv(Norm( Conv3D(Actv(Norm( ... ))) )))`
    In some cases, the first term may not need to be convoluted.
    This residual block supports users to use any depth. If depth=3, it is the same
    as bottleneck design. Deeper block means more convolutional layers.
    According to relative papers, the structure of this block has been optimized.
    Arguments for residual block:
        depth: An integer, indicates the repentance of convolutional blocks.
        ofilters: Integer, the dimensionality of the output space (i.e. the number
            of filters of output).
        lfilters: Integer, the dimensionality of the lattent space (i.e. the number
            of filters in the convolution branch).
    Arguments for convolution:
        kernel_size: An integer or tuple/list of 3 integers, specifying the
            depth, height and width of the 3D convolution window.
            Can be a single integer to specify the same value for
            all spatial dimensions.
        strides: An integer or tuple/list of 3 integers,
            specifying the strides of the convolution along each spatial
            dimension.
            Can be a single integer to specify the same value for
            all spatial dimensions.
            Specifying any stride value != 1 is incompatible with specifying
            any `dilation_rate` value != 1.
        data_format: A string,
            one of `channels_last` (default) or `channels_first`.
            The ordering of the dimensions in the inputs.
            `channels_last` corresponds to inputs with shape
            `(batch, spatial_dim1, spatial_dim2, spatial_dim3, channels)`
            while `channels_first` corresponds to inputs with shape
            `(batch, channels, spatial_dim1, spatial_dim2, spatial_dim3)`.
            It defaults to the `image_data_format` value found in your
            Keras config file at `~/.keras/keras.json`.
            If you never set it, then it will be "channels_last".
        dilation_rate: an integer or tuple/list of 3 integers, specifying
            the dilation rate to use for dilated convolution.
            Can be a single integer to specify the same value for
            all spatial dimensions.
            Currently, specifying any `dilation_rate` value != 1 is
            incompatible with specifying any stride value != 1.
        kernel_initializer: Initializer for the `kernel` weights matrix.
        kernel_regularizer: Regularizer function applied to
            the `kernel` weights matrix.
        kernel_constraint: Constraint function applied to the kernel matrix.
    Arguments for normalization:
        normalization: The normalization type, which could be
            (1) None:  do not use normalization and do not add biases.
            (2) bias:  apply biases instead of using normalization.
            (3) batch: use batch normalization.
            (4) inst : use instance normalization.
            (5) group: use group normalization.
            If using (2), the initializer, regularizer and constraint for
            beta would be applied to the bias of convolution.
        beta_initializer: Initializer for the beta weight.
        gamma_initializer: Initializer for the gamma weight.
        beta_regularizer: Optional regularizer for the beta weight.
        gamma_regularizer: Optional regularizer for the gamma weight.
        beta_constraint: Optional constraint for the beta weight.
        gamma_constraint: Optional constraint for the gamma weight.
        groups (only for group normalization): Integer, the number of 
            groups for Group Normalization.
            Can be in the range [1, N] where N is the input dimension.
            The input dimension must be divisible by the number of groups.
    Arguments for dropout: (drop out would be only applied on the entrance
                            of conv. branch.)
        dropout: The dropout type, which could be
            (1) None:    do not use dropout.
            (2) plain:   use tf.keras.layers.Dropout.
            (3) add:     use scale-invariant addictive noise.
                         (mdnt.layers.InstanceGaussianNoise)
            (4) mul:     use multiplicative noise.
                         (tf.keras.layers.GaussianDropout)
            (5) alpha:   use alpha dropout. (tf.keras.layers.AlphaDropout)
            (6) spatial: use spatial dropout (tf.keras.layers.SpatialDropout)
        dropout_rate: The drop probability. In `add` mode, it is used as
            maximal std. To learn more, please see the docstrings of each
            method.
    Arguments for activation:
        activation: Activation function to use
            (see [activations](../activations.md)).
            If you don't specify anything, no activation is applied
            (ie. "linear" activation: `a(x) = x`).
        activity_config: keywords for the parameters of activation
            function (only for lrelu).
    Arguments (others):
        activity_regularizer: Regularizer function applied to
            the output of the layer (its "activation").
            (see [regularizer](../regularizers.md)).
    Input shape:
        5D tensor with shape:
        `(samples, channels, conv_dim1, conv_dim2, conv_dim3)` if
        data_format='channels_first'
        or 5D tensor with shape:
        `(samples, conv_dim1, conv_dim2, conv_dim3, channels)` if
        data_format='channels_last'.
    Output shape:
        5D tensor with shape:
        `(samples, filters, new_conv_dim1, new_conv_dim2, new_conv_dim3)` if
        data_format='channels_first'
        or 5D tensor with shape:
        `(samples, new_conv_dim1, new_conv_dim2, new_conv_dim3, filters)` if
        data_format='channels_last'.
        `new_conv_dim1`, `new_conv_dim2` and `new_conv_dim3` values might have
        changed due to padding.
    """

    def __init__(self,
               ofilters,
               kernel_size,
               lfilters=None,
               depth=3,
               strides=(1, 1, 1),
               data_format='channels_last',
               dilation_rate=(1, 1, 1),
               kernel_initializer='glorot_uniform',
               kernel_regularizer=None,
               kernel_constraint=None,
               normalization='inst',
               beta_initializer='zeros',
               gamma_initializer='ones',
               beta_regularizer=None,
               gamma_regularizer=None,
               beta_constraint=None,
               gamma_constraint=None,
               groups=32,
               dropout=None,
               dropout_rate=0.3,
               activation=None,
               activity_config=None,
               activity_regularizer=None,
               **kwargs):
        super(Residual3D, self).__init__(
            rank=3, depth=depth, ofilters=ofilters,
            kernel_size=kernel_size,
            lfilters=lfilters,
            strides=strides,
            data_format=data_format,
            dilation_rate=dilation_rate,
            kernel_initializer=initializers.get(kernel_initializer),
            kernel_regularizer=regularizers.get(kernel_regularizer),
            kernel_constraint=constraints.get(kernel_constraint),
            normalization=normalization,
            beta_initializer=initializers.get(beta_initializer),
            gamma_initializer=initializers.get(gamma_initializer),
            beta_regularizer=regularizers.get(beta_regularizer),
            gamma_regularizer=regularizers.get(gamma_regularizer),
            beta_constraint=constraints.get(beta_constraint),
            gamma_constraint=constraints.get(gamma_constraint),
            groups=groups,
            dropout=dropout,
            dropout_rate=dropout_rate,
            activation=activation,
            activity_config=activity_config,
            activity_regularizer=regularizers.get(activity_regularizer),
            **kwargs)
            
class _ResidualTranspose(Layer):
    """Modern transposed residual layer (sometimes called residual deconvolution).
    Abstract nD residual layer (private, used as implementation base).
    `_ResidualTranspose` implements the operation:
        `output = AConv(Upsamp(input)) + Conv(Actv(Norm( conv(Actv(Norm( ... ))) )))`
    In some cases, the first term may not need to be convoluted.
    Such a structure is mainly brought from:
        Bottleneck structure: Deep Residual Learning for Image Recognition
            https://arxiv.org/abs/1512.03385
        Sublayer order: Identity Mappings in Deep Residual Networks
            https://arxiv.org/abs/1603.05027v3
    Experiments show that the aforementioned implementation may be the optimal
    design for residual block. We popularize the residual block into the case that
    enables any depth.
    Arguments for residual block:
        rank: An integer, the rank of the convolution, e.g. "2" for 2D convolution.
        depth: An integer, indicates the repentance of convolutional blocks.
        ofilters: Integer, the dimensionality of the output space (i.e. the number
            of filters of output).
        lfilters: Integer, the dimensionality of the lattent space (i.e. the number
            of filters in the convolution branch).
    Arguments for convolution:
        kernel_size: An integer or tuple/list of n integers, specifying the
            length of the convolution window.
        strides: An integer or tuple/list of n integers,
            specifying the stride length of the convolution.
            Specifying any stride value != 1 is incompatible with specifying
            any `dilation_rate` value != 1.
        output_mshape: (Only avaliable for new-style API) An integer or tuple/list
            of the desired output shape. When setting this option, `output_padding`
            and `out_cropping` would be inferred from the input shape, which means
            users' options would be invalid for the following two options.
            A recommended method of using this method is applying such a scheme:
                `AConv(..., output_mshape=tensor.get_shape())`
        output_padding: An integer or tuple/list of n integers,
            specifying the amount of padding along the axes of the output tensor.
            The amount of output padding along a given dimension must be
            lower than the stride along that same dimension.
            If set to `None` (default), the output shape would not be padded.
            (When using new-style API, the padding could be like ((a,b),(c,d),...) 
             so that you could be able to perform padding along different edges.)
        out_cropping: (Only avaliable for new-style API) An integer or tuple/list 
            of n integers, specifying the amount of cropping along the axes of the
            output tensor. The amount of output cropping along a given dimension must
            be lower than the stride along that same dimension.
            If set to `None` (default), the output shape would not be cropped.
            (Because this option only takes effect on new-style API, the cropping
             could be like ((a,b),(c,d),...) so that you could be able to perform
             cropping along different edges.)
        data_format: A string, one of `channels_last` (default) or `channels_first`.
            The ordering of the dimensions in the inputs.
            `channels_last` corresponds to inputs with shape
            `(batch, ..., channels)` while `channels_first` corresponds to
            inputs with shape `(batch, channels, ...)`.
        dilation_rate: An integer or tuple/list of n integers, specifying
            the dilation rate to use for dilated convolution.
            Currently, specifying any `dilation_rate` value != 1 is
            incompatible with specifying any `strides` value != 1.
        kernel_initializer: An initializer for the convolution kernel.
        kernel_regularizer: Optional regularizer for the convolution kernel.
        kernel_constraint: Optional projection function to be applied to the
            kernel after being updated by an `Optimizer` (e.g. used to implement
            norm constraints or value constraints for layer weights). The function
            must take as input the unprojected variable and must return the
            projected variable (which must have the same shape). Constraints are
            not safe to use when doing asynchronous distributed training.
        trainable: Boolean, if `True` also add variables to the graph collection
            `GraphKeys.TRAINABLE_VARIABLES` (see `tf.Variable`).
        name: A string, the name of the layer.
    Arguments for normalization:
        normalization: The normalization type, which could be
            (1) None:  do not use normalization and do not add biases.
            (2) bias:  apply biases instead of using normalization.
            (3) batch: use batch normalization.
            (4) inst : use instance normalization.
            (5) group: use group normalization.
            If using (2), the initializer, regularizer and constraint for
            beta would be applied to the bias of convolution.
        beta_initializer: Initializer for the beta weight.
        gamma_initializer: Initializer for the gamma weight.
        beta_regularizer: Optional regularizer for the beta weight.
        gamma_regularizer: Optional regularizer for the gamma weight.
        beta_constraint: Optional constraint for the beta weight.
        gamma_constraint: Optional constraint for the gamma weight.
        groups (only for group normalization): Integer, the number of 
            groups for Group Normalization.
            Can be in the range [1, N] where N is the input dimension.
            The input dimension must be divisible by the number of groups.
    Arguments for dropout: (drop out would be only applied on the entrance
                            of conv. branch.)
        dropout: The dropout type, which could be
            (1) None:    do not use dropout.
            (2) plain:   use tf.keras.layers.Dropout.
            (3) add:     use scale-invariant addictive noise.
                         (mdnt.layers.InstanceGaussianNoise)
            (4) mul:     use multiplicative noise.
                         (tf.keras.layers.GaussianDropout)
            (5) alpha:   use alpha dropout. (tf.keras.layers.AlphaDropout)
            (6) spatial: use spatial dropout (tf.keras.layers.SpatialDropout)
        dropout_rate: The drop probability. In `add` mode, it is used as
            maximal std. To learn more, please see the docstrings of each
            method.
    Arguments for activation:
        activation: Activation function to use
            (see [activations](../activations.md)).
            If you don't specify anything, no activation is applied
            (ie. "linear" activation: `a(x) = x`).
        activity_config: keywords for the parameters of activation
            function (only for lrelu).
    Arguments (others):
        activity_regularizer: Regularizer function applied to
            the output of the layer (its "activation").
            (see [regularizer](../regularizers.md)).
    """

    def __init__(self, rank,
                 depth, ofilters,
                 kernel_size,
                 lfilters=None,
                 strides=1,
                 output_mshape=None,
                 output_padding=None,
                 output_cropping=None,
                 data_format=None,
                 dilation_rate=1,
                 kernel_initializer='glorot_uniform',
                 kernel_regularizer=None,
                 kernel_constraint=None,
                 normalization='inst',
                 beta_initializer='zeros',
                 gamma_initializer='ones',
                 beta_regularizer=None,
                 gamma_regularizer=None,
                 beta_constraint=None,
                 gamma_constraint=None,
                 groups=32,
                 dropout=None,
                 dropout_rate=0.3,
                 activation=None,
                 activity_config=None,
                 activity_regularizer=None,
                 trainable=True,
                 name=None,
                 _high_activation=None,
                 **kwargs):
        if 'input_shape' not in kwargs and 'input_dim' in kwargs:
          kwargs['input_shape'] = (kwargs.pop('input_dim'),)

        super(_ResidualTranspose, self).__init__(trainable=trainable, name=name, **kwargs)
        # Inherit from keras.layers._Conv
        self.rank = rank
        self.depth = depth - 2
        self.ofilters = ofilters
        self.lfilters = lfilters
        if self.depth < 1:
            raise ValueError('The depth of the residual block should be >= 3.')
        self.kernel_size = conv_utils.normalize_tuple(
            kernel_size, rank, 'kernel_size')
        self.strides = conv_utils.normalize_tuple(strides, rank, 'strides')
        self.output_padding = output_padding
        self.output_mshape = None
        self.output_cropping = None
        if output_mshape:
            self.output_mshape = output_mshape
        if output_cropping:
            self.output_cropping = output_cropping
        self.data_format = conv_utils.normalize_data_format(data_format)
        if rank == 1 and self.data_format == 'channels_first':
            raise ValueError('Does not support channels_first data format for 1D case due to the limitation of upsampling method.')
        self.dilation_rate = conv_utils.normalize_tuple(
            dilation_rate, rank, 'dilation_rate')
        if (not _check_dl_func(self.dilation_rate)) and (not _check_dl_func(self.strides)):
            raise ValueError('Does not support dilation_rate when strides > 1.')
        self.kernel_initializer = initializers.get(kernel_initializer)
        self.kernel_regularizer = regularizers.get(kernel_regularizer)
        self.kernel_constraint = constraints.get(kernel_constraint)
        # Inherit from mdnt.layers.normalize
        self.normalization = normalization
        if isinstance(normalization, str) and normalization in ('batch', 'inst', 'group'):
            self.gamma_initializer = initializers.get(gamma_initializer)
            self.gamma_regularizer = regularizers.get(gamma_regularizer)
            self.gamma_constraint = constraints.get(gamma_constraint)
        else:
            self.gamma_initializer = None
            self.gamma_regularizer = None
            self.gamma_constraint = None
        self.beta_initializer = initializers.get(beta_initializer)
        self.beta_regularizer = regularizers.get(beta_regularizer)
        self.beta_constraint = constraints.get(beta_constraint)
        self.groups = groups
        # Inherit from mdnt.layers.dropout
        self.dropout = dropout
        self.dropout_rate = dropout_rate
        # Inherit from keras.engine.Layer
        if _high_activation is not None:
            activation = _high_activation
        self.high_activation = _high_activation
        if isinstance(activation, str) and (activation.casefold() in ('prelu','lrelu')):
            self.activation = activations.get(None)
            self.high_activation = activation.casefold()
            self.activity_config = activity_config # dictionary passed to activation
        elif activation is not None:
            self.activation = activations.get(activation)
            self.activity_config = None
        self.sub_activity_regularizer=regularizers.get(activity_regularizer)

        # Reserve for build()
        self.channelIn = None
        
        self.trainable = trainable
        self.input_spec = InputSpec(ndim=self.rank + 2)

    def build(self, input_shape):
        input_shape = tensor_shape.TensorShape(input_shape)
        input_shape = input_shape.with_rank_at_least(self.rank + 2)
        if self.data_format == 'channels_first':
            channel_axis = 1
        else:
            channel_axis = -1
        if input_shape.dims[channel_axis].value is None:
            raise ValueError('The channel dimension of the inputs should be defined. Found `None`.')
        self.channelIn = int(input_shape[channel_axis])
        if self.lfilters is None:
            self.lfilters = max( 1, self.channelIn // 2 )
        # If setting output_mshape, need to infer output_padding & output_cropping
        if self.output_mshape is not None:
            if not isinstance(self.output_mshape, (list, tuple)):
                l_output_mshape = self.output_mshape.as_list()
            else:
                l_output_mshape = self.output_mshape
            l_output_mshape = l_output_mshape[1:-1]
            l_input_shape = input_shape.as_list()[1:-1]
            self.output_padding = []
            self.output_cropping = []
            for i in range(self.rank):
                get_shape_diff = l_output_mshape[i] - l_input_shape[i]*max(self.strides[i], self.dilation_rate[i])
                if get_shape_diff > 0:
                    b_inf = get_shape_diff // 2
                    b_sup = b_inf + get_shape_diff % 2
                    self.output_padding.append((b_inf, b_sup))
                    self.output_cropping.append((0, 0))
                elif get_shape_diff < 0:
                    get_shape_diff = -get_shape_diff
                    b_inf = get_shape_diff // 2
                    b_sup = b_inf + get_shape_diff % 2
                    self.output_cropping.append((b_inf, b_sup))
                    self.output_padding.append((0, 0))
                else:
                    self.output_cropping.append((0, 0))
                    self.output_padding.append((0, 0))
            deFlag_padding = 0
            deFlag_cropping = 0
            for i in range(self.rank):
                smp = self.output_padding[i]
                if smp[0] == 0 and smp[1] == 0:
                    deFlag_padding += 1
                smp = self.output_cropping[i]
                if smp[0] == 0 and smp[1] == 0:
                    deFlag_cropping += 1
            if deFlag_padding >= self.rank:
                self.output_padding = None
            else:
                self.output_padding = tuple(self.output_padding)
            if deFlag_cropping >= self.rank:
                self.output_cropping = None
            else:
                self.output_cropping = tuple(self.output_cropping)
        if self.rank == 1:
            self.layer_uppool = UpSampling1D(size=self.strides[0])
            self.layer_uppool.build(input_shape)
            next_shape = self.layer_uppool.compute_output_shape(input_shape)
            if self.output_padding is not None:
                self.layer_padding = ZeroPadding1D(padding=self.output_padding)[0] # Necessary for 1D case, because we need to pick (a,b) from ((a, b))
                self.layer_padding.build(next_shape)
                next_shape = self.layer_padding.compute_output_shape(next_shape)
            else:
                self.layer_padding = None
        elif self.rank == 2:
            self.layer_uppool = UpSampling2D(size=self.strides, data_format=self.data_format)
            self.layer_uppool.build(input_shape)
            next_shape = self.layer_uppool.compute_output_shape(input_shape)
            if self.output_padding is not None:
                self.layer_padding = ZeroPadding2D(padding=self.output_padding, data_format=self.data_format)
                self.layer_padding.build(next_shape)
                next_shape = self.layer_padding.compute_output_shape(next_shape)
            else:
                self.layer_padding = None
        elif self.rank == 3:
            self.layer_uppool = UpSampling3D(size=self.strides, data_format=self.data_format)
            self.layer_uppool.build(input_shape)
            next_shape = self.layer_uppool.compute_output_shape(input_shape)
            if self.output_padding is not None:
                self.layer_padding = ZeroPadding3D(padding=self.output_padding, data_format=self.data_format)
                self.layer_padding.build(next_shape)
                next_shape = self.layer_padding.compute_output_shape(next_shape)
            else:
                self.layer_padding = None
        else:
            raise ValueError('Rank of the deconvolution should be 1, 2 or 3.')
        last_use_bias = True
        if self.ofilters == self.channelIn:
            self.layer_branch_left = None
            left_shape = next_shape
        else:
            last_use_bias = False
            self.layer_branch_left = _AConv(rank = self.rank,
                          filters = self.ofilters,
                          kernel_size = 1,
                          strides = 1,
                          padding = 'same',
                          data_format = self.data_format,
                          dilation_rate = 1,
                          kernel_initializer=self.kernel_initializer,
                          kernel_regularizer=self.kernel_regularizer,
                          kernel_constraint=self.kernel_constraint,
                          normalization=self.normalization,
                          beta_initializer=self.beta_initializer,
                          gamma_initializer=self.gamma_initializer,
                          beta_regularizer=self.beta_regularizer,
                          gamma_regularizer=self.gamma_regularizer,
                          beta_constraint=self.beta_constraint,
                          gamma_constraint=self.gamma_constraint,
                          groups=self.groups,
                          activation=None,
                          activity_config=None,
                          activity_regularizer=None,
                          _high_activation=None,
                          trainable=self.trainable)
            self.layer_branch_left.build(next_shape)
            compat.collect_properties(self, self.layer_branch_left) # for compatibility
            left_shape = self.layer_branch_left.compute_output_shape(next_shape)
        # Right branch, with dropout
        self.layer_dropout = return_dropout(self.dropout, self.dropout_rate, axis=channel_axis, rank=self.rank)
        if self.layer_dropout is not None:
            self.layer_dropout.build(next_shape)
            right_shape = self.layer_dropout.compute_output_shape(next_shape)
        else:
            right_shape = next_shape
        self.layer_first = NACUnit(rank = self.rank,
                          filters = self.lfilters,
                          kernel_size = 1,
                          strides = 1,
                          padding = 'same',
                          data_format = self.data_format,
                          dilation_rate = 1,
                          kernel_initializer=self.kernel_initializer,
                          kernel_regularizer=self.kernel_regularizer,
                          kernel_constraint=self.kernel_constraint,
                          normalization=self.normalization,
                          beta_initializer=self.beta_initializer,
                          beta_regularizer=self.beta_regularizer,
                          beta_constraint=self.beta_constraint,
                          groups=self.groups,
                          activation=self.activation,
                          activity_config=self.activity_config,
                          activity_regularizer=self.sub_activity_regularizer,
                          _high_activation=self.high_activation,
                          trainable=self.trainable)
        self.layer_first.build(right_shape)
        compat.collect_properties(self, self.layer_first) # for compatibility
        right_shape = self.layer_first.compute_output_shape(right_shape)
        # Repeat blocks by depth number
        for i in range(self.depth):
            if i == 0:
                sub_dilation_rate = self.dilation_rate
            else:
                sub_dilation_rate = 1
            layer_middle = NACUnit(rank = self.rank,
                          filters = self.lfilters,
                          kernel_size = self.kernel_size,
                          strides = 1,
                          padding = 'same',
                          data_format = self.data_format,
                          dilation_rate = sub_dilation_rate,
                          kernel_initializer=self.kernel_initializer,
                          kernel_regularizer=self.kernel_regularizer,
                          kernel_constraint=self.kernel_constraint,
                          normalization=self.normalization,
                          beta_initializer=self.beta_initializer,
                          gamma_initializer=self.gamma_initializer,
                          beta_regularizer=self.beta_regularizer,
                          gamma_regularizer=self.gamma_regularizer,
                          beta_constraint=self.beta_constraint,
                          gamma_constraint=self.gamma_constraint,
                          groups=self.groups,
                          activation=self.activation,
                          activity_config=self.activity_config,
                          activity_regularizer=self.sub_activity_regularizer,
                          _high_activation=self.high_activation,
                          trainable=self.trainable)
            layer_middle.build(right_shape)
            compat.collect_properties(self, layer_middle) # for compatibility
            right_shape = layer_middle.compute_output_shape(right_shape)
            setattr(self, 'layer_middle_{0:02d}'.format(i), layer_middle)
        self.layer_last = NACUnit(rank = self.rank,
                          filters = self.ofilters,
                          kernel_size = 1,
                          strides = 1,
                          padding = 'same',
                          data_format = self.data_format,
                          dilation_rate = 1,
                          kernel_initializer=self.kernel_initializer,
                          kernel_regularizer=self.kernel_regularizer,
                          kernel_constraint=self.kernel_constraint,
                          normalization=self.normalization,
                          beta_initializer=self.beta_initializer,
                          beta_regularizer=self.beta_regularizer,
                          beta_constraint=self.beta_constraint,
                          groups=self.groups,
                          activation=self.activation,
                          activity_config=self.activity_config,
                          activity_regularizer=self.sub_activity_regularizer,
                          _high_activation=self.high_activation,
                          _use_bias=last_use_bias,
                          trainable=self.trainable)
        self.layer_last.build(right_shape)
        compat.collect_properties(self, self.layer_last) # for compatibility
        right_shape = self.layer_last.compute_output_shape(right_shape)
        self.layer_merge = Add()
        self.layer_merge.build([left_shape, right_shape])
        next_shape = self.layer_merge.compute_output_shape([left_shape, right_shape])
        if self.output_cropping is not None:
            if self.rank == 1:
                self.layer_cropping = Cropping1D(cropping=self.output_cropping)[0]
            elif self.rank == 2:
                self.layer_cropping = Cropping2D(cropping=self.output_cropping)
            elif self.rank == 3:
                self.layer_cropping = Cropping3D(cropping=self.output_cropping)
            else:
                raise ValueError('Rank of the deconvolution should be 1, 2 or 3.')
            self.layer_cropping.build(next_shape)
            next_shape = self.layer_cropping.compute_output_shape(next_shape)
        else:
            self.layer_cropping = None
        super(_ResidualTranspose, self).build(input_shape)

    def call(self, inputs):
        outputs = self.layer_uppool(inputs)
        if self.layer_padding is not None:
            outputs = self.layer_padding(outputs)
        if self.layer_branch_left is not None:
            branch_left = self.layer_branch_left(outputs)
        else:
            branch_left = outputs
        if self.layer_dropout is not None:
            branch_right = self.layer_dropout(outputs)
        else:
            branch_right = outputs
        branch_right = self.layer_first(branch_right)
        for i in range(self.depth):
            layer_middle = getattr(self, 'layer_middle_{0:02d}'.format(i))
            branch_right = layer_middle(branch_right)
        branch_right = self.layer_last(branch_right)
        outputs = self.layer_merge([branch_left, branch_right])
        if self.layer_cropping is not None:
            outputs = self.layer_cropping(outputs)
        return outputs

    def compute_output_shape(self, input_shape):
        input_shape = tensor_shape.TensorShape(input_shape)
        input_shape = input_shape.with_rank_at_least(self.rank + 2)
        next_shape = self.layer_uppool.compute_output_shape(input_shape)
        if self.layer_padding is not None:
            next_shape = self.layer_padding.compute_output_shape(next_shape)
        if self.layer_branch_left is not None:
            branch_left_shape = self.layer_branch_left.compute_output_shape(next_shape)
        else:
            branch_left_shape = next_shape
        if self.layer_dropout is not None:
            branch_right_shape = self.layer_dropout.compute_output_shape(next_shape)
        else:
            branch_right_shape = next_shape
        branch_right_shape = self.layer_first.compute_output_shape(branch_right_shape)
        for i in range(self.depth):
            layer_middle = getattr(self, 'layer_middle_{0:02d}'.format(i))
            branch_right_shape = layer_middle.compute_output_shape(branch_right_shape)
        branch_right_shape = self.layer_last.compute_output_shape(branch_right_shape)
        next_shape = self.layer_merge.compute_output_shape([branch_left_shape, branch_right_shape])
        if self.layer_cropping is not None:
            next_shape = self.layer_cropping.compute_output_shape(next_shape)
        return next_shape
    
    def get_config(self):
        config = {
            'depth': self.depth + 2,
            'ofilters': self.ofilters,
            'lfilters': self.lfilters,
            'kernel_size': self.kernel_size,
            'strides': self.strides,
            'output_mshape': self.output_mshape,
            'output_padding': self.output_padding,
            'output_cropping': self.output_cropping,
            'data_format': self.data_format,
            'dilation_rate': self.dilation_rate,
            'kernel_initializer': initializers.serialize(self.kernel_initializer),
            'kernel_regularizer': regularizers.serialize(self.kernel_regularizer),
            'kernel_constraint': constraints.serialize(self.kernel_constraint),
            'normalization': self.normalization,
            'beta_initializer': initializers.serialize(self.beta_initializer),
            'gamma_initializer': initializers.serialize(self.gamma_initializer),
            'beta_regularizer': regularizers.serialize(self.beta_regularizer),
            'gamma_regularizer': regularizers.serialize(self.gamma_regularizer),
            'beta_constraint': constraints.serialize(self.beta_constraint),
            'gamma_constraint': constraints.serialize(self.gamma_constraint),
            'groups': self.groups,
            'dropout': self.dropout,
            'dropout_rate': self.dropout_rate,
            'activation': activations.serialize(self.activation),
            'activity_config': self.activity_config,
            'activity_regularizer': regularizers.serialize(self.activity_regularizer),
            '_high_activation': self.high_activation
        }
        base_config = super(_ResidualTranspose, self).get_config()
        return dict(list(base_config.items()) + list(config.items()))
        
class Residual1DTranspose(_ResidualTranspose):
    """Modern transposed residual layer (sometimes called residual deconvolution).
    `Residual1DTranspose` implements the operation:
        `output = AConv1D(Upsamp(input)) + Conv1D(Actv(Norm( Conv1D(Actv(Norm( ... ))) )))`
    In some cases, the first term may not need to be convoluted.
    This residual block supports users to use any depth. If depth=3, it is the same
    as bottleneck design. Deeper block means more convolutional layers.
    According to relative papers, the structure of this block has been optimized.
    The upsampling is performed on the input layer. Previous works prove that the
    "transposed convolution" could be viewed as upsampling + plain convolution. Here
    we adopt such a technique to realize this upsampling architecture.
    Arguments for residual block:
        depth: An integer, indicates the repentance of convolutional blocks.
        ofilters: Integer, the dimensionality of the output space (i.e. the number
            of filters of output).
        lfilters: Integer, the dimensionality of the lattent space (i.e. the number
            of filters in the convolution branch).
    Arguments for convolution:
        kernel_size: An integer or tuple/list of n integers, specifying the
            length of the convolution window.
        strides: An integer or tuple/list of n integers,
            specifying the stride length of the convolution.
            Specifying any stride value != 1 is incompatible with specifying
            any `dilation_rate` value != 1.
        output_mshape: (Only avaliable for new-style API) An integer or tuple/list
            of the desired output shape. When setting this option, `output_padding`
            and `out_cropping` would be inferred from the input shape, which means
            users' options would be invalid for the following two options.
            A recommended method of using this method is applying such a scheme:
                `AConv(..., output_mshape=tensor.get_shape())`
        output_padding: An integer or tuple/list of n integers,
            specifying the amount of padding along the height and width
            of the output tensor.
            The amount of output padding along a given dimension must be
            lower than the stride along that same dimension.
            If set to `None` (default), the output shape would not be padded.
        out_cropping: (Only avaliable for new-style API) An integer or tuple/list 
            of n integers, specifying the amount of cropping along the axes of the
            output tensor. The amount of output cropping along a given dimension must
            be lower than the stride along that same dimension.
            If set to `None` (default), the output shape would not be cropped.
        data_format: A string, only support `channels_last` here:
            `channels_last` corresponds to inputs with shape
            `(batch, steps channels)`
        dilation_rate: An integer or tuple/list of n integers, specifying
            the dilation rate to use for dilated convolution.
            Currently, specifying any `dilation_rate` value != 1 is
            incompatible with specifying any `strides` value != 1.
        kernel_initializer: An initializer for the convolution kernel.
        kernel_regularizer: Optional regularizer for the convolution kernel.
        kernel_constraint: Optional projection function to be applied to the
            kernel after being updated by an `Optimizer` (e.g. used to implement
            norm constraints or value constraints for layer weights). The function
            must take as input the unprojected variable and must return the
            projected variable (which must have the same shape). Constraints are
            not safe to use when doing asynchronous distributed training.
        trainable: Boolean, if `True` also add variables to the graph collection
            `GraphKeys.TRAINABLE_VARIABLES` (see `tf.Variable`).
        name: A string, the name of the layer.
    Arguments for normalization:
        normalization: The normalization type, which could be
            (1) None:  do not use normalization and do not add biases.
            (2) bias:  apply biases instead of using normalization.
            (3) batch: use batch normalization.
            (4) inst : use instance normalization.
            (5) group: use group normalization.
            If using (2), the initializer, regularizer and constraint for
            beta would be applied to the bias of convolution.
        beta_initializer: Initializer for the beta weight.
        gamma_initializer: Initializer for the gamma weight.
        beta_regularizer: Optional regularizer for the beta weight.
        gamma_regularizer: Optional regularizer for the gamma weight.
        beta_constraint: Optional constraint for the beta weight.
        gamma_constraint: Optional constraint for the gamma weight.
        groups (only for group normalization): Integer, the number of 
            groups for Group Normalization.
            Can be in the range [1, N] where N is the input dimension.
            The input dimension must be divisible by the number of groups.
    Arguments for dropout: (drop out would be only applied on the entrance
                            of conv. branch.)
        dropout: The dropout type, which could be
            (1) None:    do not use dropout.
            (2) plain:   use tf.keras.layers.Dropout.
            (3) add:     use scale-invariant addictive noise.
                         (mdnt.layers.InstanceGaussianNoise)
            (4) mul:     use multiplicative noise.
                         (tf.keras.layers.GaussianDropout)
            (5) alpha:   use alpha dropout. (tf.keras.layers.AlphaDropout)
            (6) spatial: use spatial dropout (tf.keras.layers.SpatialDropout)
        dropout_rate: The drop probability. In `add` mode, it is used as
            maximal std. To learn more, please see the docstrings of each
            method.
    Arguments for activation:
        activation: Activation function to use
            (see [activations](../activations.md)).
            If you don't specify anything, no activation is applied
            (ie. "linear" activation: `a(x) = x`).
        activity_config: keywords for the parameters of activation
            function (only for lrelu).
    Arguments (others):
        activity_regularizer: Regularizer function applied to
            the output of the layer (its "activation").
            (see [regularizer](../regularizers.md)).
    Input shape:
        3D tensor with shape: `(batch_size, steps, input_dim)`
    Output shape:
        3D tensor with shape: `(batch_size, new_steps, filters)`
        `steps` value might have changed due to padding or strides.
    """

    def __init__(self, ofilters,
                 kernel_size,
                 lfilters=None,
                 depth=3,
                 strides=1,
                 output_mshape=None,
                 output_padding=None,
                 output_cropping=None,
                 data_format=None,
                 dilation_rate=1,
                 kernel_initializer='glorot_uniform',
                 kernel_regularizer=None,
                 kernel_constraint=None,
                 normalization='inst',
                 beta_initializer='zeros',
                 gamma_initializer='ones',
                 beta_regularizer=None,
                 gamma_regularizer=None,
                 beta_constraint=None,
                 gamma_constraint=None,
                 groups=32,
                 dropout=None,
                 dropout_rate=0.3,
                 activation=None,
                 activity_config=None,
                 activity_regularizer=None,
                 **kwargs):
        super(Residual1DTranspose, self).__init__(
            rank=1, depth=depth, ofilters=ofilters,
            kernel_size=kernel_size,
            lfilters=lfilters,
            strides=strides,
            output_mshape=output_mshape,
            output_padding=output_padding,
            output_cropping=output_cropping,
            data_format=data_format,
            dilation_rate=dilation_rate,
            kernel_initializer=initializers.get(kernel_initializer),
            kernel_regularizer=regularizers.get(kernel_regularizer),
            kernel_constraint=constraints.get(kernel_constraint),
            normalization=normalization,
            beta_initializer=initializers.get(beta_initializer),
            gamma_initializer=initializers.get(gamma_initializer),
            beta_regularizer=regularizers.get(beta_regularizer),
            gamma_regularizer=regularizers.get(gamma_regularizer),
            beta_constraint=constraints.get(beta_constraint),
            gamma_constraint=constraints.get(gamma_constraint),
            groups=groups,
            dropout=dropout,
            dropout_rate=dropout_rate,
            activation=activation,
            activity_config=activity_config,
            activity_regularizer=regularizers.get(activity_regularizer),
            **kwargs)
            
class Residual2DTranspose(_ResidualTranspose):
    """Modern transposed residual layer (sometimes called residual deconvolution).
    `Residual2DTranspose` implements the operation:
        `output = AConv2D(Upsamp(input)) + Conv2D(Actv(Norm( Conv2D(Actv(Norm( ... ))) )))`
    In some cases, the first term may not need to be convoluted.
    This residual block supports users to use any depth. If depth=3, it is the same
    as bottleneck design. Deeper block means more convolutional layers.
    According to relative papers, the structure of this block has been optimized.
    The upsampling is performed on the input layer. Previous works prove that the
    "transposed convolution" could be viewed as upsampling + plain convolution. Here
    we adopt such a technique to realize this upsampling architecture.
    Arguments for residual block:
        depth: An integer, indicates the repentance of convolutional blocks.
        ofilters: Integer, the dimensionality of the output space (i.e. the number
            of filters of output).
        lfilters: Integer, the dimensionality of the lattent space (i.e. the number
            of filters in the convolution branch).
    Arguments for convolution:
        kernel_size: An integer or tuple/list of 2 integers, specifying the
            height and width of the 2D convolution window.
            Can be a single integer to specify the same value for
            all spatial dimensions.
        strides: An integer or tuple/list of 2 integers,
            specifying the strides of the convolution along the height and width.
            Can be a single integer to specify the same value for
            all spatial dimensions.
            Specifying any stride value != 1 is incompatible with specifying
            any `dilation_rate` value != 1.
        output_mshape: (Only avaliable for new-style API) An integer or tuple/list
            of the desired output shape. When setting this option, `output_padding`
            and `out_cropping` would be inferred from the input shape, which means
            users' options would be invalid for the following two options.
            A recommended method of using this method is applying such a scheme:
                `AConv(..., output_mshape=tensor.get_shape())`
        output_padding: An integer or tuple/list of 2 integers,
            specifying the amount of padding along the height and width
            of the output tensor.
            Can be a single integer to specify the same value for all
            spatial dimensions.
            The amount of output padding along a given dimension must be
            lower than the stride along that same dimension.
            If set to `None` (default), the output shape would not be padded.
        out_cropping: (Only avaliable for new-style API) An integer or tuple/list 
            of n integers, specifying the amount of cropping along the axes of the
            output tensor. The amount of output cropping along a given dimension must
            be lower than the stride along that same dimension.
            If set to `None` (default), the output shape would not be cropped.
        data_format: A string,
            one of `channels_last` (default) or `channels_first`.
            The ordering of the dimensions in the inputs.
            `channels_last` corresponds to inputs with shape
            `(batch, height, width, channels)` while `channels_first`
            corresponds to inputs with shape
            `(batch, channels, height, width)`.
            It defaults to the `image_data_format` value found in your
            Keras config file at `~/.keras/keras.json`.
            If you never set it, then it will be "channels_last".
        dilation_rate: an integer or tuple/list of 2 integers, specifying
            the dilation rate to use for dilated convolution.
            Can be a single integer to specify the same value for
            all spatial dimensions.
            Currently, specifying any `dilation_rate` value != 1 is
            incompatible with specifying any stride value != 1.
        kernel_initializer: An initializer for the convolution kernel.
        kernel_regularizer: Optional regularizer for the convolution kernel.
        kernel_constraint: Optional projection function to be applied to the
            kernel after being updated by an `Optimizer` (e.g. used to implement
            norm constraints or value constraints for layer weights). The function
            must take as input the unprojected variable and must return the
            projected variable (which must have the same shape). Constraints are
            not safe to use when doing asynchronous distributed training.
        trainable: Boolean, if `True` also add variables to the graph collection
            `GraphKeys.TRAINABLE_VARIABLES` (see `tf.Variable`).
        name: A string, the name of the layer.
    Arguments for normalization:
        normalization: The normalization type, which could be
            (1) None:  do not use normalization and do not add biases.
            (2) bias:  apply biases instead of using normalization.
            (3) batch: use batch normalization.
            (4) inst : use instance normalization.
            (5) group: use group normalization.
            If using (2), the initializer, regularizer and constraint for
            beta would be applied to the bias of convolution.
        beta_initializer: Initializer for the beta weight.
        gamma_initializer: Initializer for the gamma weight.
        beta_regularizer: Optional regularizer for the beta weight.
        gamma_regularizer: Optional regularizer for the gamma weight.
        beta_constraint: Optional constraint for the beta weight.
        gamma_constraint: Optional constraint for the gamma weight.
        groups (only for group normalization): Integer, the number of 
            groups for Group Normalization.
            Can be in the range [1, N] where N is the input dimension.
            The input dimension must be divisible by the number of groups.
    Arguments for dropout: (drop out would be only applied on the entrance
                            of conv. branch.)
        dropout: The dropout type, which could be
            (1) None:    do not use dropout.
            (2) plain:   use tf.keras.layers.Dropout.
            (3) add:     use scale-invariant addictive noise.
                         (mdnt.layers.InstanceGaussianNoise)
            (4) mul:     use multiplicative noise.
                         (tf.keras.layers.GaussianDropout)
            (5) alpha:   use alpha dropout. (tf.keras.layers.AlphaDropout)
            (6) spatial: use spatial dropout (tf.keras.layers.SpatialDropout)
        dropout_rate: The drop probability. In `add` mode, it is used as
            maximal std. To learn more, please see the docstrings of each
            method.
    Arguments for activation:
        activation: Activation function to use
            (see [activations](../activations.md)).
            If you don't specify anything, no activation is applied
            (ie. "linear" activation: `a(x) = x`).
        activity_config: keywords for the parameters of activation
            function (only for lrelu).
    Arguments (others):
        activity_regularizer: Regularizer function applied to
            the output of the layer (its "activation").
            (see [regularizer](../regularizers.md)).
    Input shape:
        4D tensor with shape:
        `(batch, channels, rows, cols)` if data_format='channels_first'
        or 4D tensor with shape:
        `(batch, rows, cols, channels)` if data_format='channels_last'.
    Output shape:
        4D tensor with shape:
        `(batch, filters, new_rows, new_cols)` if data_format='channels_first'
        or 4D tensor with shape:
        `(batch, new_rows, new_cols, filters)` if data_format='channels_last'.
        `rows` and `cols` values might have changed due to padding.
    """

    def __init__(self, ofilters,
                 kernel_size,
                 lfilters=None,
                 depth=3,
                 strides=(1, 1),
                 output_mshape=None,
                 output_padding=None,
                 output_cropping=None,
                 data_format=None,
                 dilation_rate=(1, 1),
                 kernel_initializer='glorot_uniform',
                 kernel_regularizer=None,
                 kernel_constraint=None,
                 normalization='inst',
                 beta_initializer='zeros',
                 gamma_initializer='ones',
                 beta_regularizer=None,
                 gamma_regularizer=None,
                 beta_constraint=None,
                 gamma_constraint=None,
                 groups=32,
                 dropout=None,
                 dropout_rate=0.3,
                 activation=None,
                 activity_config=None,
                 activity_regularizer=None,
                 **kwargs):
        super(Residual2DTranspose, self).__init__(
            rank=2, depth=depth, ofilters=ofilters,
            kernel_size=kernel_size,
            lfilters=lfilters,
            strides=strides,
            output_mshape=output_mshape,
            output_padding=output_padding,
            output_cropping=output_cropping,
            data_format=data_format,
            dilation_rate=dilation_rate,
            kernel_initializer=initializers.get(kernel_initializer),
            kernel_regularizer=regularizers.get(kernel_regularizer),
            kernel_constraint=constraints.get(kernel_constraint),
            normalization=normalization,
            beta_initializer=initializers.get(beta_initializer),
            gamma_initializer=initializers.get(gamma_initializer),
            beta_regularizer=regularizers.get(beta_regularizer),
            gamma_regularizer=regularizers.get(gamma_regularizer),
            beta_constraint=constraints.get(beta_constraint),
            gamma_constraint=constraints.get(gamma_constraint),
            groups=groups,
            dropout=dropout,
            dropout_rate=dropout_rate,
            activation=activation,
            activity_config=activity_config,
            activity_regularizer=regularizers.get(activity_regularizer),
            **kwargs)
            
class Residual3DTranspose(_ResidualTranspose):
    """Modern transposed residual layer (sometimes called residual deconvolution).
    `Residual3DTranspose` implements the operation:
        `output = AConv3D(Upsamp(input)) + Conv3D(Actv(Norm( Conv3D(Actv(Norm( ... ))) )))`
    In some cases, the first term may not need to be convoluted.
    This residual block supports users to use any depth. If depth=3, it is the same
    as bottleneck design. Deeper block means more convolutional layers.
    According to relative papers, the structure of this block has been optimized.
    The upsampling is performed on the input layer. Previous works prove that the
    "transposed convolution" could be viewed as upsampling + plain convolution. Here
    we adopt such a technique to realize this upsampling architecture.
    Arguments for residual block:
        depth: An integer, indicates the repentance of convolutional blocks.
        ofilters: Integer, the dimensionality of the output space (i.e. the number
            of filters of output).
        lfilters: Integer, the dimensionality of the lattent space (i.e. the number
            of filters in the convolution branch).
    Arguments for convolution:
        kernel_size: An integer or tuple/list of 3 integers, specifying the
            depth, height and width of the 3D convolution window.
            Can be a single integer to specify the same value for
            all spatial dimensions.
        strides: An integer or tuple/list of 3 integers,
            specifying the strides of the convolution along the depth, height
            and width.
            Can be a single integer to specify the same value for
            all spatial dimensions.
            Specifying any stride value != 1 is incompatible with specifying
            any `dilation_rate` value != 1.
        output_mshape: (Only avaliable for new-style API) An integer or tuple/list
            of the desired output shape. When setting this option, `output_padding`
            and `out_cropping` would be inferred from the input shape, which means
            users' options would be invalid for the following two options.
            A recommended method of using this method is applying such a scheme:
                `AConv(..., output_mshape=tensor.get_shape())`
        output_padding: An integer or tuple/list of 3 integers,
            specifying the amount of padding along the depth, height, and
            width.
            Can be a single integer to specify the same value for all
            spatial dimensions.
            The amount of output padding along a given dimension must be
            lower than the stride along that same dimension.
            If set to `None` (default), the output shape is inferred.
        out_cropping: (Only avaliable for new-style API) An integer or tuple/list 
            of n integers, specifying the amount of cropping along the axes of the
            output tensor. The amount of output cropping along a given dimension must
            be lower than the stride along that same dimension.
            If set to `None` (default), the output shape would not be cropped.
        data_format: A string,
            one of `channels_last` (default) or `channels_first`.
            The ordering of the dimensions in the inputs.
            `channels_last` corresponds to inputs with shape
            `(batch, depth, height, width, channels)` while `channels_first`
            corresponds to inputs with shape
            `(batch, channels, depth, height, width)`.
            It defaults to the `image_data_format` value found in your
            Keras config file at `~/.keras/keras.json`.
            If you never set it, then it will be "channels_last".
        dilation_rate: an integer or tuple/list of 3 integers, specifying
            the dilation rate to use for dilated convolution.
            Can be a single integer to specify the same value for
            all spatial dimensions.
            Currently, specifying any `dilation_rate` value != 1 is
            incompatible with specifying any stride value != 1.
        kernel_initializer: An initializer for the convolution kernel.
        kernel_regularizer: Optional regularizer for the convolution kernel.
        kernel_constraint: Optional projection function to be applied to the
            kernel after being updated by an `Optimizer` (e.g. used to implement
            norm constraints or value constraints for layer weights). The function
            must take as input the unprojected variable and must return the
            projected variable (which must have the same shape). Constraints are
            not safe to use when doing asynchronous distributed training.
        trainable: Boolean, if `True` also add variables to the graph collection
            `GraphKeys.TRAINABLE_VARIABLES` (see `tf.Variable`).
        name: A string, the name of the layer.
    Arguments for normalization:
        normalization: The normalization type, which could be
            (1) None:  do not use normalization and do not add biases.
            (2) bias:  apply biases instead of using normalization.
            (3) batch: use batch normalization.
            (4) inst : use instance normalization.
            (5) group: use group normalization.
            If using (2), the initializer, regularizer and constraint for
            beta would be applied to the bias of convolution.
        beta_initializer: Initializer for the beta weight.
        gamma_initializer: Initializer for the gamma weight.
        beta_regularizer: Optional regularizer for the beta weight.
        gamma_regularizer: Optional regularizer for the gamma weight.
        beta_constraint: Optional constraint for the beta weight.
        gamma_constraint: Optional constraint for the gamma weight.
        groups (only for group normalization): Integer, the number of 
            groups for Group Normalization.
            Can be in the range [1, N] where N is the input dimension.
            The input dimension must be divisible by the number of groups.\
    Arguments for dropout: (drop out would be only applied on the entrance
                            of conv. branch.)
        dropout: The dropout type, which could be
            (1) None:    do not use dropout.
            (2) plain:   use tf.keras.layers.Dropout.
            (3) add:     use scale-invariant addictive noise.
                         (mdnt.layers.InstanceGaussianNoise)
            (4) mul:     use multiplicative noise.
                         (tf.keras.layers.GaussianDropout)
            (5) alpha:   use alpha dropout. (tf.keras.layers.AlphaDropout)
            (6) spatial: use spatial dropout (tf.keras.layers.SpatialDropout)
        dropout_rate: The drop probability. In `add` mode, it is used as
            maximal std. To learn more, please see the docstrings of each
            method.
    Arguments for activation:
        activation: Activation function to use
            (see [activations](../activations.md)).
            If you don't specify anything, no activation is applied
            (ie. "linear" activation: `a(x) = x`).
        activity_config: keywords for the parameters of activation
            function (only for lrelu).
    Arguments (others):
        activity_regularizer: Regularizer function applied to
            the output of the layer (its "activation").
            (see [regularizer](../regularizers.md)).
    Input shape:
        5D tensor with shape:
        `(batch, channels, depth, rows, cols)` if data_format='channels_first'
        or 5D tensor with shape:
        `(batch, depth, rows, cols, channels)` if data_format='channels_last'.
    Output shape:
        5D tensor with shape:
        `(batch, filters, new_depth, new_rows, new_cols)` if
        data_format='channels_first'
        or 5D tensor with shape:
        `(batch, new_depth, new_rows, new_cols, filters)` if
        data_format='channels_last'.
        `depth` and `rows` and `cols` values might have changed due to padding.
    """

    def __init__(self, ofilters,
                 kernel_size,
                 lfilters=None,
                 depth=3,
                 strides=(1, 1, 1),
                 output_mshape=None,
                 output_padding=None,
                 output_cropping=None,
                 data_format=None,
                 dilation_rate=(1, 1, 1),
                 kernel_initializer='glorot_uniform',
                 kernel_regularizer=None,
                 kernel_constraint=None,
                 normalization='inst',
                 beta_initializer='zeros',
                 gamma_initializer='ones',
                 beta_regularizer=None,
                 gamma_regularizer=None,
                 beta_constraint=None,
                 gamma_constraint=None,
                 groups=32,
                 dropout=None,
                 dropout_rate=0.3,
                 activation=None,
                 activity_config=None,
                 activity_regularizer=None,
                 **kwargs):
        super(Residual3DTranspose, self).__init__(
            rank=3, depth=depth, ofilters=ofilters,
            kernel_size=kernel_size,
            lfilters=lfilters,
            strides=strides,
            output_mshape=output_mshape,
            output_padding=output_padding,
            output_cropping=output_cropping,
            data_format=data_format,
            dilation_rate=dilation_rate,
            kernel_initializer=initializers.get(kernel_initializer),
            kernel_regularizer=regularizers.get(kernel_regularizer),
            kernel_constraint=constraints.get(kernel_constraint),
            normalization=normalization,
            beta_initializer=initializers.get(beta_initializer),
            gamma_initializer=initializers.get(gamma_initializer),
            beta_regularizer=regularizers.get(beta_regularizer),
            gamma_regularizer=regularizers.get(gamma_regularizer),
            beta_constraint=constraints.get(beta_constraint),
            gamma_constraint=constraints.get(gamma_constraint),
            groups=groups,
            dropout=dropout,
            dropout_rate=dropout_rate,
            activation=activation,
            activity_config=activity_config,
            activity_regularizer=regularizers.get(activity_regularizer),
            **kwargs)

class _Resnext(Layer):
    """Modern ResNeXt layer.
    Abstract nD ResNeXt layer (private, used as implementation base).
    `_ResNeXt` implements the operation:
        `output = AConv(input) + Conv(Actv(Norm(AGPConv(AGPConv( ... )))))`
    where `AGPConv` means advanced group convolution. It could be formulated by
        `AGPConv(.) = GPConv(Actv(Norm(.)))`
    `GPConv` is group convolution. The whole structure of the convolution part in
    ResNeXt could be viewed as group convoltions inside the bottleneck structure.
    In some cases, the first term may not need to be convoluted.
    Such a structure is mainly brought from:
        Aggregated Residual Transformations for Deep Neural Networks
            https://arxiv.org/abs/1611.05431
    Arguments for residual block:
        rank: An integer, the rank of the convolution, e.g. "2" for 2D convolution.
        depth: An integer, indicates the repentance of convolutional blocks.
        ofilters: Integer, the dimensionality of the output space (i.e. the number
            of filters of output).
        lgroups: Integer, the group number of the latent convolution branch. The
            number of filters in the whole latent space is lgroups * lfilters.
        lfilters: Integer, the dimensionality in each the lattent group (i.e. the
            number of filters in each latent convolution branch).
    Arguments for convolution:
        kernel_size: An integer or tuple/list of n integers, specifying the
            length of the convolution window.
        strides: An integer or tuple/list of n integers,
            specifying the stride length of the convolution.
            Specifying any stride value != 1 is incompatible with specifying
            any `dilation_rate` value != 1.
        data_format: A string, one of `channels_last` (default) or `channels_first`.
            The ordering of the dimensions in the inputs.
            `channels_last` corresponds to inputs with shape
            `(batch, ..., channels)` while `channels_first` corresponds to
            inputs with shape `(batch, channels, ...)`.
        dilation_rate: An integer or tuple/list of n integers, specifying
            the dilation rate to use for dilated convolution.
            Currently, specifying any `dilation_rate` value != 1 is
            incompatible with specifying any `strides` value != 1.
        kernel_initializer: An initializer for the convolution kernel.
        kernel_regularizer: Optional regularizer for the convolution kernel.
        kernel_constraint: Optional projection function to be applied to the
            kernel after being updated by an `Optimizer` (e.g. used to implement
            norm constraints or value constraints for layer weights). The function
            must take as input the unprojected variable and must return the
            projected variable (which must have the same shape). Constraints are
            not safe to use when doing asynchronous distributed training.
        trainable: Boolean, if `True` also add variables to the graph collection
            `GraphKeys.TRAINABLE_VARIABLES` (see `tf.Variable`).
        name: A string, the name of the layer.
    Arguments for normalization:
        normalization: The normalization type, which could be
            (1) None:  do not use normalization and do not add biases.
            (2) bias:  apply biases instead of using normalization.
            (3) batch: use batch normalization.
            (4) inst : use instance normalization.
            (5) group: use group normalization.
            If using (2), the initializer, regularizer and constraint for
            beta would be applied to the bias of convolution.
        beta_initializer: Initializer for the beta weight.
        gamma_initializer: Initializer for the gamma weight.
        beta_regularizer: Optional regularizer for the beta weight.
        gamma_regularizer: Optional regularizer for the gamma weight.
        beta_constraint: Optional constraint for the beta weight.
        gamma_constraint: Optional constraint for the gamma weight.
        groups (only for group normalization): Integer, the number of 
            groups for Group Normalization.
            Can be in the range [1, N] where N is the input dimension.
            The input dimension must be divisible by the number of groups.
    Arguments for dropout: (drop out would be only applied on the entrance
                            of conv. branch.)
        dropout: The dropout type, which could be
            (1) None:    do not use dropout.
            (2) plain:   use tf.keras.layers.Dropout.
            (3) add:     use scale-invariant addictive noise.
                         (mdnt.layers.InstanceGaussianNoise)
            (4) mul:     use multiplicative noise.
                         (tf.keras.layers.GaussianDropout)
            (5) alpha:   use alpha dropout. (tf.keras.layers.AlphaDropout)
            (6) spatial: use spatial dropout (tf.keras.layers.SpatialDropout)
        dropout_rate: The drop probability. In `add` mode, it is used as
            maximal std. To learn more, please see the docstrings of each
            method.
    Arguments for activation:
        activation: Activation function to use
            (see [activations](../activations.md)).
            If you don't specify anything, no activation is applied
            (ie. "linear" activation: `a(x) = x`).
        activity_config: keywords for the parameters of activation
            function (only for lrelu).
    Arguments (others):
        activity_regularizer: Regularizer function applied to
            the output of the layer (its "activation").
            (see [regularizer](../regularizers.md)).
    """

    def __init__(self, rank,
                 depth, ofilters,
                 kernel_size,
                 lgroups=None, lfilters=None,
                 strides=1,
                 data_format=None,
                 dilation_rate=1,
                 kernel_initializer='glorot_uniform',
                 kernel_regularizer=None,
                 kernel_constraint=None,
                 normalization='inst',
                 beta_initializer='zeros',
                 gamma_initializer='ones',
                 beta_regularizer=None,
                 gamma_regularizer=None,
                 beta_constraint=None,
                 gamma_constraint=None,
                 groups=32,
                 dropout=None,
                 dropout_rate=0.3,
                 activation=None,
                 activity_config=None,
                 activity_regularizer=None,
                 trainable=True,
                 name=None,
                 _high_activation=None,
                 **kwargs):
        if 'input_shape' not in kwargs and 'input_dim' in kwargs:
          kwargs['input_shape'] = (kwargs.pop('input_dim'),)

        super(_Resnext, self).__init__(trainable=trainable, name=name, **kwargs)
        # Inherit from keras.layers._Conv
        self.rank = rank
        self.depth = depth - 2
        self.ofilters = ofilters
        self.lgroups = lgroups
        self.lfilters = lfilters
        if self.depth < 1:
            raise ValueError('The depth of the ResNeXt block should be >= 3.')
        self.kernel_size = conv_utils.normalize_tuple(
            kernel_size, rank, 'kernel_size')
        self.strides = conv_utils.normalize_tuple(strides, rank, 'strides')
        self.data_format = conv_utils.normalize_data_format(data_format)
        self.dilation_rate = conv_utils.normalize_tuple(
            dilation_rate, rank, 'dilation_rate')
        if (not _check_dl_func(self.dilation_rate)) and (not _check_dl_func(self.strides)):
            raise ValueError('Does not support dilation_rate when strides > 1.')
        self.kernel_initializer = initializers.get(kernel_initializer)
        self.kernel_regularizer = regularizers.get(kernel_regularizer)
        self.kernel_constraint = constraints.get(kernel_constraint)
        self.activity_regularizer = regularizers.get(activity_regularizer)
        # Inherit from mdnt.layers.normalize
        self.normalization = normalization
        if isinstance(normalization, str) and normalization in ('batch', 'inst', 'group'):
            self.gamma_initializer = initializers.get(gamma_initializer)
            self.gamma_regularizer = regularizers.get(gamma_regularizer)
            self.gamma_constraint = constraints.get(gamma_constraint)
        else:
            self.gamma_initializer = None
            self.gamma_regularizer = None
            self.gamma_constraint = None
        self.beta_initializer = initializers.get(beta_initializer)
        self.beta_regularizer = regularizers.get(beta_regularizer)
        self.beta_constraint = constraints.get(beta_constraint)
        self.groups = groups
        # Inherit from mdnt.layers.dropout
        self.dropout = dropout
        self.dropout_rate = dropout_rate
        # Inherit from keras.engine.Layer
        if _high_activation is not None:
            activation = _high_activation
        self.high_activation = _high_activation
        if isinstance(activation, str) and (activation.casefold() in ('prelu','lrelu')):
            self.activation = activations.get(None)
            self.high_activation = activation.casefold()
            self.activity_config = activity_config # dictionary passed to activation
        elif activation is not None:
            self.activation = activations.get(activation)
            self.activity_config = None
        self.sub_activity_regularizer=regularizers.get(activity_regularizer)

        # Reserve for build()
        self.channelIn = None
        
        self.trainable = trainable
        self.input_spec = InputSpec(ndim=self.rank + 2)

    def build(self, input_shape):
        input_shape = tensor_shape.TensorShape(input_shape)
        input_shape = input_shape.with_rank_at_least(self.rank + 2)
        if self.data_format == 'channels_first':
            channel_axis = 1
        else:
            channel_axis = -1
        if input_shape.dims[channel_axis].value is None:
            raise ValueError('The channel dimension of the inputs should be defined. Found `None`.')
        self.channelIn = int(input_shape[channel_axis])
        if (self.lgroups is None) or (self.lfilters is None):
            if (self.lgroups is None) and (self.lfilters is None):
                self.lgroups = 32
            if self.lfilters is None:
                cal_lfilters = self.channelIn / 2
                cal_lfilters = _cal_quad_root(a=self.depth*_get_prod(self.kernel_size)*self.lgroups, 
                               b=(self.channelIn+self.ofilters)*self.lgroups, 
                               c=-cal_lfilters*(self.channelIn+self.ofilters+self.depth*_get_prod(self.kernel_size)*cal_lfilters))
                self.lfilters = max( 1, int(round(cal_lfilters)) )
            elif self.lgroups is None:
                cal_lgroups = self.channelIn / 2
                cal_lgroups = (cal_lgroups/self.lfilters)*(self.depth*_get_prod(self.kernel_size)*cal_lgroups+self.channelIn+self.ofilters)/(self.depth*_get_prod(self.kernel_size)*self.lfilters+self.channelIn+self.ofilters)
                self.lgroups = max( 1, int(round(cal_lgroups)) )
        wholeLfilters = self.lgroups * self.lfilters
        last_use_bias = True
        if _check_dl_func(self.strides) and self.ofilters == self.channelIn:
            self.layer_branch_left = None
            left_shape = input_shape
        else:
            last_use_bias = False
            self.layer_branch_left = _AConv(rank = self.rank,
                          filters = self.ofilters,
                          kernel_size = 1,
                          strides = self.strides,
                          padding = 'same',
                          data_format = self.data_format,
                          dilation_rate = 1,
                          kernel_initializer=self.kernel_initializer,
                          kernel_regularizer=self.kernel_regularizer,
                          kernel_constraint=self.kernel_constraint,
                          normalization=self.normalization,
                          beta_initializer=self.beta_initializer,
                          gamma_initializer=self.gamma_initializer,
                          beta_regularizer=self.beta_regularizer,
                          gamma_regularizer=self.gamma_regularizer,
                          beta_constraint=self.beta_constraint,
                          gamma_constraint=self.gamma_constraint,
                          groups=self.groups,
                          activation=None,
                          activity_config=None,
                          activity_regularizer=None,
                          _high_activation=None,
                          trainable=self.trainable)
            self.layer_branch_left.build(input_shape)
            compat.collect_properties(self, self.layer_branch_left) # for compatibility
            left_shape = self.layer_branch_left.compute_output_shape(input_shape)
        # The right branch is divided into many groups
        # Right branch, with dropout
        self.layer_dropout = return_dropout(self.dropout, self.dropout_rate, axis=channel_axis, rank=self.rank)
        if self.layer_dropout is not None:
            self.layer_dropout.build(input_shape)
            right_shape = self.layer_dropout.compute_output_shape(input_shape)
        else:
            right_shape = input_shape
        self.layer_first = NACUnit(rank = self.rank,
                        filters = wholeLfilters,
                        kernel_size = 1,
                        strides = self.strides,
                        padding = 'same',
                        data_format = self.data_format,
                        dilation_rate = 1,
                        kernel_initializer=self.kernel_initializer,
                        kernel_regularizer=self.kernel_regularizer,
                        kernel_constraint=self.kernel_constraint,
                        normalization=self.normalization,
                        beta_initializer=self.beta_initializer,
                        gamma_initializer=self.gamma_initializer,
                        beta_regularizer=self.beta_regularizer,
                        gamma_regularizer=self.gamma_regularizer,
                        beta_constraint=self.beta_constraint,
                        gamma_constraint=self.gamma_constraint,
                        groups=self.groups,
                        activation=self.activation,
                        activity_config=self.activity_config,
                        activity_regularizer=self.sub_activity_regularizer,
                        _high_activation=self.high_activation,
                        trainable=self.trainable)
        self.layer_first.build(right_shape)
        compat.collect_properties(self, self.layer_first) # for compatibility
        right_shape = self.layer_first.compute_output_shape(right_shape)
        # Repeat blocks by depth number
        for i in range(self.depth):
            if i == 0:
                sub_dilation_rate = self.dilation_rate
            else:
                sub_dilation_rate = 1
            layer_middle = NACUnit(rank = self.rank,
                                   filters = wholeLfilters,
                                   lgroups = self.lgroups,
                                   kernel_size = self.kernel_size,
                                   strides = 1,
                                   padding = 'same',
                                   data_format = self.data_format,
                                   dilation_rate = sub_dilation_rate,
                                   kernel_initializer=self.kernel_initializer,
                                   kernel_regularizer=self.kernel_regularizer,
                                   kernel_constraint=self.kernel_constraint,
                                   normalization=self.normalization,
                                   beta_initializer=self.beta_initializer,
                                   gamma_initializer=self.gamma_initializer,
                                   beta_regularizer=self.beta_regularizer,
                                   gamma_regularizer=self.gamma_regularizer,
                                   beta_constraint=self.beta_constraint,
                                   gamma_constraint=self.gamma_constraint,
                                   groups=self.groups,
                                   activation=self.activation,
                                   activity_config=self.activity_config,
                                   activity_regularizer=self.sub_activity_regularizer,
                                   _high_activation=self.high_activation,
                                   trainable=self.trainable)
            layer_middle.build(right_shape)
            compat.collect_properties(self, layer_middle) # for compatibility
            right_shape = layer_middle.compute_output_shape(right_shape)
            setattr(self, 'layer_middle_{0:02d}'.format(i+1), layer_middle)
        self.layer_last = NACUnit(rank = self.rank,
                          filters = self.ofilters,
                          kernel_size = 1,
                          strides = 1,
                          padding = 'same',
                          data_format = self.data_format,
                          dilation_rate = 1,
                          kernel_initializer=self.kernel_initializer,
                          kernel_regularizer=self.kernel_regularizer,
                          kernel_constraint=self.kernel_constraint,
                          normalization=self.normalization,
                          beta_initializer=self.beta_initializer,
                          gamma_initializer=self.gamma_initializer,
                          beta_regularizer=self.beta_regularizer,
                          gamma_regularizer=self.gamma_regularizer,
                          beta_constraint=self.beta_constraint,
                          gamma_constraint=self.gamma_constraint,
                          groups=self.groups,
                          activation=self.activation,
                          activity_config=self.activity_config,
                          activity_regularizer=self.sub_activity_regularizer,
                          _high_activation=self.high_activation,
                          _use_bias=last_use_bias,
                          trainable=self.trainable)
        self.layer_last.build(right_shape)
        compat.collect_properties(self, self.layer_last) # for compatibility
        right_shape = self.layer_last.compute_output_shape(right_shape)
        self.layer_merge = Add()
        self.layer_merge.build([left_shape, right_shape])
        super(_Resnext, self).build(input_shape)

    def call(self, inputs):
        if self.layer_branch_left is not None:
            branch_left = self.layer_branch_left(inputs)
        else:
            branch_left = inputs
        if self.layer_dropout is not None:
            branch_right = self.layer_dropout(inputs)
        else:
            branch_right = inputs
        branch_right = self.layer_first(branch_right)
        for i in range(self.depth):
            layer_middle = getattr(self, 'layer_middle_{0:02d}'.format(i+1))
            branch_right = layer_middle(branch_right)
        branch_right = self.layer_last(branch_right)
        outputs = self.layer_merge([branch_left, branch_right])
        return outputs

    def compute_output_shape(self, input_shape):
        if self.layer_branch_left is not None:
            branch_left_shape = self.layer_branch_left.compute_output_shape(input_shape)
        else:
            branch_left_shape = input_shape
        if self.layer_dropout is not None:
            branch_right_shape = self.layer_dropout.compute_output_shape(input_shape)
        else:
            branch_right_shape = input_shape
        branch_right_shape = self.layer_first.compute_output_shape(branch_right_shape)
        for i in range(self.depth):
            layer_middle = getattr(self, 'layer_middle_{0:02d}'.format(i+1))
            branch_right_shape = layer_middle.compute_output_shape(branch_right_shape)
        branch_right_shape = self.layer_last.compute_output_shape(branch_right_shape)
        next_shape = self.layer_merge.compute_output_shape([branch_left_shape, branch_right_shape])
        return next_shape
    
    def get_config(self):
        config = {
            'depth': self.depth + 2,
            'ofilters': self.ofilters,
            'lgroups': self.lgroups,
            'lfilters': self.lfilters,
            'kernel_size': self.kernel_size,
            'strides': self.strides,
            'data_format': self.data_format,
            'dilation_rate': self.dilation_rate,
            'kernel_initializer': initializers.serialize(self.kernel_initializer),
            'kernel_regularizer': regularizers.serialize(self.kernel_regularizer),
            'kernel_constraint': constraints.serialize(self.kernel_constraint),
            'normalization': self.normalization,
            'beta_initializer': initializers.serialize(self.beta_initializer),
            'gamma_initializer': initializers.serialize(self.gamma_initializer),
            'beta_regularizer': regularizers.serialize(self.beta_regularizer),
            'gamma_regularizer': regularizers.serialize(self.gamma_regularizer),
            'beta_constraint': constraints.serialize(self.beta_constraint),
            'gamma_constraint': constraints.serialize(self.gamma_constraint),
            'groups': self.groups,
            'dropout': self.dropout,
            'dropout_rate': self.dropout_rate,
            'activation': activations.serialize(self.activation),
            'activity_config': self.activity_config,
            'activity_regularizer': regularizers.serialize(self.sub_activity_regularizer),
            '_high_activation': self.high_activation
        }
        base_config = super(_Resnext, self).get_config()
        return dict(list(base_config.items()) + list(config.items()))
        
class Resnext1D(_Resnext):
    """1D ResNeXt layer.
    `Resnext1D` implements the operation:
        `output = AConv1D(input) + Conv1D(Actv(Norm(AGPConv1D(AGPConv1D( ... )))))`
    where `AGPConv1D` means advanced group convolution. It could be formulated by
        `AGPConv1D(.) = GPConv1D(Actv(Norm(.)))`
    `GPConv1D` is 1D group convolution. The whole structure of the convolution part in
    ResNeXt could be viewed as group convoltions inside the bottleneck structure.
    In some cases, the first term may not need to be convoluted.
    Arguments for residual block:
        depth: An integer, indicates the repentance of convolutional blocks.
        ofilters: Integer, the dimensionality of the output space (i.e. the number
            of filters of output).
        lgroups: Integer, the group number of the latent convolution branch. The
            number of filters in the whole latent space is lgroups * lfilters.
        lfilters: Integer, the dimensionality in each the lattent group (i.e. the
            number of filters in each latent convolution branch).
    Arguments for convolution:
        kernel_size: An integer or tuple/list of a single integer,
            specifying the length of the 1D convolution window.
        strides: An integer or tuple/list of a single integer,
            specifying the stride length of the convolution.
            Specifying any stride value != 1 is incompatible with specifying
            any `dilation_rate` value != 1.
        data_format: A string,
            one of `channels_last` (default) or `channels_first`.
        dilation_rate: an integer or tuple/list of a single integer, specifying
            the dilation rate to use for dilated convolution.
            Currently, specifying any `dilation_rate` value != 1 is
            incompatible with specifying any `strides` value != 1.
        kernel_initializer: Initializer for the `kernel` weights matrix.
        kernel_regularizer: Regularizer function applied to
            the `kernel` weights matrix.
        kernel_constraint: Constraint function applied to the kernel matrix.
    Arguments for normalization:
        normalization: The normalization type, which could be
            (1) None:  do not use normalization and do not add biases.
            (2) bias:  apply biases instead of using normalization.
            (3) batch: use batch normalization.
            (4) inst : use instance normalization.
            (5) group: use group normalization.
            If using (2), the initializer, regularizer and constraint for
            beta would be applied to the bias of convolution.
        beta_initializer: Initializer for the beta weight.
        gamma_initializer: Initializer for the gamma weight.
        beta_regularizer: Optional regularizer for the beta weight.
        gamma_regularizer: Optional regularizer for the gamma weight.
        beta_constraint: Optional constraint for the beta weight.
        gamma_constraint: Optional constraint for the gamma weight.
        groups (only for group normalization): Integer, the number of 
            groups for Group Normalization.
            Can be in the range [1, N] where N is the input dimension.
            The input dimension must be divisible by the number of groups.
    Arguments for dropout: (drop out would be only applied on the entrance
                            of conv. branch.)
        dropout: The dropout type, which could be
            (1) None:    do not use dropout.
            (2) plain:   use tf.keras.layers.Dropout.
            (3) add:     use scale-invariant addictive noise.
                         (mdnt.layers.InstanceGaussianNoise)
            (4) mul:     use multiplicative noise.
                         (tf.keras.layers.GaussianDropout)
            (5) alpha:   use alpha dropout. (tf.keras.layers.AlphaDropout)
            (6) spatial: use spatial dropout (tf.keras.layers.SpatialDropout)
        dropout_rate: The drop probability. In `add` mode, it is used as
            maximal std. To learn more, please see the docstrings of each
            method.
    Arguments for activation:
        activation: Activation function to use
            (see [activations](../activations.md)).
            If you don't specify anything, no activation is applied
            (ie. "linear" activation: `a(x) = x`).
        activity_config: keywords for the parameters of activation
            function (only for lrelu).
    Arguments (others):
        activity_regularizer: Regularizer function applied to
            the output of the layer (its "activation").
            (see [regularizer](../regularizers.md)).
    Input shape:
        3D tensor with shape: `(batch_size, steps, input_dim)`
    Output shape:
        3D tensor with shape: `(batch_size, new_steps, filters)`
        `steps` value might have changed due to padding or strides.
    """

    def __init__(self,
               ofilters,
               kernel_size,
               lgroups=None,
               lfilters=None,
               depth=3,
               strides=1,
               data_format='channels_last',
               dilation_rate=1,
               kernel_initializer='glorot_uniform',
               kernel_regularizer=None,
               kernel_constraint=None,
               normalization='inst',
               beta_initializer='zeros',
               gamma_initializer='ones',
               beta_regularizer=None,
               gamma_regularizer=None,
               beta_constraint=None,
               gamma_constraint=None,
               groups=32,
               dropout=None,
               dropout_rate=0.3,
               activation=None,
               activity_config=None,
               activity_regularizer=None,
               **kwargs):
        super(Resnext1D, self).__init__(
            rank=1, depth=depth, ofilters=ofilters,
            kernel_size=kernel_size,
            lgroups=lgroups, lfilters=lfilters,
            strides=strides,
            data_format=data_format,
            dilation_rate=dilation_rate,
            kernel_initializer=initializers.get(kernel_initializer),
            kernel_regularizer=regularizers.get(kernel_regularizer),
            kernel_constraint=constraints.get(kernel_constraint),
            normalization=normalization,
            beta_initializer=initializers.get(beta_initializer),
            gamma_initializer=initializers.get(gamma_initializer),
            beta_regularizer=regularizers.get(beta_regularizer),
            gamma_regularizer=regularizers.get(gamma_regularizer),
            beta_constraint=constraints.get(beta_constraint),
            gamma_constraint=constraints.get(gamma_constraint),
            groups=groups,
            dropout=dropout,
            dropout_rate=dropout_rate,
            activation=activation,
            activity_config=activity_config,
            activity_regularizer=regularizers.get(activity_regularizer),
            **kwargs)
        
class Resnext2D(_Resnext):
    """2D ResNeXt layer (e.g. spatial convolution over images).
    `Resnext2D` implements the operation:
        `output = AConv2D(input) + Conv2D(Actv(Norm(AGPConv2D(AGPConv2D( ... )))))`
    where `AGPConv2D` means advanced group convolution. It could be formulated by
        `AGPConv2D(.) = GPConv2D(Actv(Norm(.)))`
    `GPConv2D` is 2D group convolution. The whole structure of the convolution part in
    ResNeXt could be viewed as group convoltions inside the bottleneck structure.
    In some cases, the first term may not need to be convoluted.
    Arguments for residual block:
        depth: An integer, indicates the repentance of convolutional blocks.
        ofilters: Integer, the dimensionality of the output space (i.e. the number
            of filters of output).
        lgroups: Integer, the group number of the latent convolution branch. The
            number of filters in the whole latent space is lgroups * lfilters.
        lfilters: Integer, the dimensionality in each the lattent group (i.e. the
            number of filters in each latent convolution branch).
    Arguments for convolution:
        kernel_size: An integer or tuple/list of 2 integers, specifying the
            height and width of the 2D convolution window.
            Can be a single integer to specify the same value for
            all spatial dimensions.
        strides: An integer or tuple/list of 2 integers,
            specifying the strides of the convolution along the height and width.
            Can be a single integer to specify the same value for
            all spatial dimensions.
            Specifying any stride value != 1 is incompatible with specifying
            any `dilation_rate` value != 1.
        data_format: A string,
            one of `channels_last` (default) or `channels_first`.
            The ordering of the dimensions in the inputs.
            `channels_last` corresponds to inputs with shape
            `(batch, height, width, channels)` while `channels_first`
            corresponds to inputs with shape
            `(batch, channels, height, width)`.
            It defaults to the `image_data_format` value found in your
            Keras config file at `~/.keras/keras.json`.
            If you never set it, then it will be "channels_last".
        dilation_rate: an integer or tuple/list of 2 integers, specifying
            the dilation rate to use for dilated convolution.
            Can be a single integer to specify the same value for
            all spatial dimensions.
            Currently, specifying any `dilation_rate` value != 1 is
            incompatible with specifying any stride value != 1.
        kernel_initializer: Initializer for the `kernel` weights matrix.
        kernel_regularizer: Regularizer function applied to
            the `kernel` weights matrix.
        kernel_constraint: Constraint function applied to the kernel matrix.
    Arguments for normalization:
        normalization: The normalization type, which could be
            (1) None:  do not use normalization and do not add biases.
            (2) bias:  apply biases instead of using normalization.
            (3) batch: use batch normalization.
            (4) inst : use instance normalization.
            (5) group: use group normalization.
            If using (2), the initializer, regularizer and constraint for
            beta would be applied to the bias of convolution.
        beta_initializer: Initializer for the beta weight.
        gamma_initializer: Initializer for the gamma weight.
        beta_regularizer: Optional regularizer for the beta weight.
        gamma_regularizer: Optional regularizer for the gamma weight.
        beta_constraint: Optional constraint for the beta weight.
        gamma_constraint: Optional constraint for the gamma weight.
        groups (only for group normalization): Integer, the number of 
            groups for Group Normalization.
            Can be in the range [1, N] where N is the input dimension.
            The input dimension must be divisible by the number of groups.
    Arguments for dropout: (drop out would be only applied on the entrance
                            of conv. branch.)
        dropout: The dropout type, which could be
            (1) None:    do not use dropout.
            (2) plain:   use tf.keras.layers.Dropout.
            (3) add:     use scale-invariant addictive noise.
                         (mdnt.layers.InstanceGaussianNoise)
            (4) mul:     use multiplicative noise.
                         (tf.keras.layers.GaussianDropout)
            (5) alpha:   use alpha dropout. (tf.keras.layers.AlphaDropout)
            (6) spatial: use spatial dropout (tf.keras.layers.SpatialDropout)
        dropout_rate: The drop probability. In `add` mode, it is used as
            maximal std. To learn more, please see the docstrings of each
            method.
    Arguments for activation:
        activation: Activation function to use
            (see [activations](../activations.md)).
            If you don't specify anything, no activation is applied
            (ie. "linear" activation: `a(x) = x`).
        activity_config: keywords for the parameters of activation
            function (only for lrelu).
    Arguments (others):
        activity_regularizer: Regularizer function applied to
            the output of the layer (its "activation").
            (see [regularizer](../regularizers.md)).
    Input shape:
        4D tensor with shape:
        `(samples, channels, rows, cols)` if data_format='channels_first'
        or 4D tensor with shape:
        `(samples, rows, cols, channels)` if data_format='channels_last'.
    Output shape:
        4D tensor with shape:
        `(samples, filters, new_rows, new_cols)` if data_format='channels_first'
        or 4D tensor with shape:
        `(samples, new_rows, new_cols, filters)` if data_format='channels_last'.
        `rows` and `cols` values might have changed due to padding.
    """

    def __init__(self,
               ofilters,
               kernel_size,
               lgroups=None, lfilters=None,
               depth=3,
               strides=(1, 1),
               data_format='channels_last',
               dilation_rate=(1, 1),
               kernel_initializer='glorot_uniform',
               kernel_regularizer=None,
               kernel_constraint=None,
               normalization='inst',
               beta_initializer='zeros',
               gamma_initializer='ones',
               beta_regularizer=None,
               gamma_regularizer=None,
               beta_constraint=None,
               gamma_constraint=None,
               groups=32,
               dropout=None,
               dropout_rate=0.3,
               activation=None,
               activity_config=None,
               activity_regularizer=None,
               **kwargs):
        super(Resnext2D, self).__init__(
            rank=2, depth=depth, ofilters=ofilters,
            kernel_size=kernel_size,
            lgroups=lgroups, lfilters=lfilters,
            strides=strides,
            data_format=data_format,
            dilation_rate=dilation_rate,
            kernel_initializer=initializers.get(kernel_initializer),
            kernel_regularizer=regularizers.get(kernel_regularizer),
            kernel_constraint=constraints.get(kernel_constraint),
            normalization=normalization,
            beta_initializer=initializers.get(beta_initializer),
            gamma_initializer=initializers.get(gamma_initializer),
            beta_regularizer=regularizers.get(beta_regularizer),
            gamma_regularizer=regularizers.get(gamma_regularizer),
            beta_constraint=constraints.get(beta_constraint),
            gamma_constraint=constraints.get(gamma_constraint),
            groups=groups,
            dropout=dropout,
            dropout_rate=dropout_rate,
            activation=activation,
            activity_config=activity_config,
            activity_regularizer=regularizers.get(activity_regularizer),
            **kwargs)
        
class Resnext3D(_Resnext):
    """3D ResNeXt layer (e.g. spatial convolution over volumes).
    `Resnext3D` implements the operation:
        `output = AConv3D(input) + Conv3D(Actv(Norm(AGPConv3D(AGPConv3D( ... )))))`
    where `AGPConv3D` means advanced group convolution. It could be formulated by
        `AGPConv3D(.) = GPConv3D(Actv(Norm(.)))`
    `GPConv3D` is 3D group convolution. The whole structure of the convolution part in
    ResNeXt could be viewed as group convoltions inside the bottleneck structure.
    In some cases, the first term may not need to be convoluted.
    Arguments for residual block:
        depth: An integer, indicates the repentance of convolutional blocks.
        ofilters: Integer, the dimensionality of the output space (i.e. the number
            of filters of output).
        lgroups: Integer, the group number of the latent convolution branch. The
            number of filters in the whole latent space is lgroups * lfilters.
        lfilters: Integer, the dimensionality in each the lattent group (i.e. the
            number of filters in each latent convolution branch).
    Arguments for convolution:
        kernel_size: An integer or tuple/list of 3 integers, specifying the
            depth, height and width of the 3D convolution window.
            Can be a single integer to specify the same value for
            all spatial dimensions.
        strides: An integer or tuple/list of 3 integers,
            specifying the strides of the convolution along each spatial
            dimension.
            Can be a single integer to specify the same value for
            all spatial dimensions.
            Specifying any stride value != 1 is incompatible with specifying
            any `dilation_rate` value != 1.
        data_format: A string,
            one of `channels_last` (default) or `channels_first`.
            The ordering of the dimensions in the inputs.
            `channels_last` corresponds to inputs with shape
            `(batch, spatial_dim1, spatial_dim2, spatial_dim3, channels)`
            while `channels_first` corresponds to inputs with shape
            `(batch, channels, spatial_dim1, spatial_dim2, spatial_dim3)`.
            It defaults to the `image_data_format` value found in your
            Keras config file at `~/.keras/keras.json`.
            If you never set it, then it will be "channels_last".
        dilation_rate: an integer or tuple/list of 3 integers, specifying
            the dilation rate to use for dilated convolution.
            Can be a single integer to specify the same value for
            all spatial dimensions.
            Currently, specifying any `dilation_rate` value != 1 is
            incompatible with specifying any stride value != 1.
        kernel_initializer: Initializer for the `kernel` weights matrix.
        kernel_regularizer: Regularizer function applied to
            the `kernel` weights matrix.
        kernel_constraint: Constraint function applied to the kernel matrix.
    Arguments for normalization:
        normalization: The normalization type, which could be
            (1) None:  do not use normalization and do not add biases.
            (2) bias:  apply biases instead of using normalization.
            (3) batch: use batch normalization.
            (4) inst : use instance normalization.
            (5) group: use group normalization.
            If using (2), the initializer, regularizer and constraint for
            beta would be applied to the bias of convolution.
        beta_initializer: Initializer for the beta weight.
        gamma_initializer: Initializer for the gamma weight.
        beta_regularizer: Optional regularizer for the beta weight.
        gamma_regularizer: Optional regularizer for the gamma weight.
        beta_constraint: Optional constraint for the beta weight.
        gamma_constraint: Optional constraint for the gamma weight.
        groups (only for group normalization): Integer, the number of 
            groups for Group Normalization.
            Can be in the range [1, N] where N is the input dimension.
            The input dimension must be divisible by the number of groups.
    Arguments for dropout: (drop out would be only applied on the entrance
                            of conv. branch.)
        dropout: The dropout type, which could be
            (1) None:    do not use dropout.
            (2) plain:   use tf.keras.layers.Dropout.
            (3) add:     use scale-invariant addictive noise.
                         (mdnt.layers.InstanceGaussianNoise)
            (4) mul:     use multiplicative noise.
                         (tf.keras.layers.GaussianDropout)
            (5) alpha:   use alpha dropout. (tf.keras.layers.AlphaDropout)
            (6) spatial: use spatial dropout (tf.keras.layers.SpatialDropout)
        dropout_rate: The drop probability. In `add` mode, it is used as
            maximal std. To learn more, please see the docstrings of each
            method.
    Arguments for activation:
        activation: Activation function to use
            (see [activations](../activations.md)).
            If you don't specify anything, no activation is applied
            (ie. "linear" activation: `a(x) = x`).
        activity_config: keywords for the parameters of activation
            function (only for lrelu).
    Arguments (others):
        activity_regularizer: Regularizer function applied to
            the output of the layer (its "activation").
            (see [regularizer](../regularizers.md)).
    Input shape:
        5D tensor with shape:
        `(samples, channels, conv_dim1, conv_dim2, conv_dim3)` if
        data_format='channels_first'
        or 5D tensor with shape:
        `(samples, conv_dim1, conv_dim2, conv_dim3, channels)` if
        data_format='channels_last'.
    Output shape:
        5D tensor with shape:
        `(samples, filters, new_conv_dim1, new_conv_dim2, new_conv_dim3)` if
        data_format='channels_first'
        or 5D tensor with shape:
        `(samples, new_conv_dim1, new_conv_dim2, new_conv_dim3, filters)` if
        data_format='channels_last'.
        `new_conv_dim1`, `new_conv_dim2` and `new_conv_dim3` values might have
        changed due to padding.
    """

    def __init__(self,
               ofilters,
               kernel_size,
               lgroups=None, lfilters=None,
               depth=3,
               strides=(1, 1, 1),
               data_format='channels_last',
               dilation_rate=(1, 1, 1),
               kernel_initializer='glorot_uniform',
               kernel_regularizer=None,
               kernel_constraint=None,
               normalization='inst',
               beta_initializer='zeros',
               gamma_initializer='ones',
               beta_regularizer=None,
               gamma_regularizer=None,
               beta_constraint=None,
               gamma_constraint=None,
               groups=32,
               dropout=None,
               dropout_rate=0.3,
               activation=None,
               activity_config=None,
               activity_regularizer=None,
               **kwargs):
        super(Resnext3D, self).__init__(
            rank=3, depth=depth, ofilters=ofilters,
            kernel_size=kernel_size,
            lgroups=lgroups, lfilters=lfilters,
            strides=strides,
            data_format=data_format,
            dilation_rate=dilation_rate,
            kernel_initializer=initializers.get(kernel_initializer),
            kernel_regularizer=regularizers.get(kernel_regularizer),
            kernel_constraint=constraints.get(kernel_constraint),
            normalization=normalization,
            beta_initializer=initializers.get(beta_initializer),
            gamma_initializer=initializers.get(gamma_initializer),
            beta_regularizer=regularizers.get(beta_regularizer),
            gamma_regularizer=regularizers.get(gamma_regularizer),
            beta_constraint=constraints.get(beta_constraint),
            gamma_constraint=constraints.get(gamma_constraint),
            groups=groups,
            dropout=dropout,
            dropout_rate=dropout_rate,
            activation=activation,
            activity_config=activity_config,
            activity_regularizer=regularizers.get(activity_regularizer),
            **kwargs)
            
class _ResnextTranspose(Layer):
    """Modern transposed ResNeXt layer (sometimes called ResNeXt deconvolution).
    Abstract nD ResNeXt layer (private, used as implementation base).
    `_ResnextTranspose` implements the operation:
        `output = AConv(Upsamp(input)) + Conv(Actv(Norm(AGPConv(AGPConv( ... )))))`
    In some cases, the first term may not need to be convoluted.
    The transposed ResNeXt block is realized by simply adding upsamping on the in-
    put layer, because previous works show that the transposed convolution is eqi-
    valent to upsampling + convolution.
    Arguments for residual block:
        rank: An integer, the rank of the convolution, e.g. "2" for 2D convolution.
        depth: An integer, indicates the repentance of convolutional blocks.
        ofilters: Integer, the dimensionality of the output space (i.e. the number
            of filters of output).
        lgroups: Integer, the group number of the latent convolution branch. The
            number of filters in the whole latent space is lgroups * lfilters.
        lfilters: Integer, the dimensionality in each the lattent group (i.e. the
            number of filters in each latent convolution branch).
    Arguments for convolution:
        kernel_size: An integer or tuple/list of n integers, specifying the
            length of the convolution window.
        strides: An integer or tuple/list of n integers,
            specifying the stride length of the convolution.
            Specifying any stride value != 1 is incompatible with specifying
            any `dilation_rate` value != 1.
        output_mshape: (Only avaliable for new-style API) An integer or tuple/list
            of the desired output shape. When setting this option, `output_padding`
            and `out_cropping` would be inferred from the input shape, which means
            users' options would be invalid for the following two options.
            A recommended method of using this method is applying such a scheme:
                `AConv(..., output_mshape=tensor.get_shape())`
        output_padding: An integer or tuple/list of n integers,
            specifying the amount of padding along the axes of the output tensor.
            The amount of output padding along a given dimension must be
            lower than the stride along that same dimension.
            If set to `None` (default), the output shape would not be padded.
            (When using new-style API, the padding could be like ((a,b),(c,d),...) 
             so that you could be able to perform padding along different edges.)
        out_cropping: (Only avaliable for new-style API) An integer or tuple/list 
            of n integers, specifying the amount of cropping along the axes of the
            output tensor. The amount of output cropping along a given dimension must
            be lower than the stride along that same dimension.
            If set to `None` (default), the output shape would not be cropped.
            (Because this option only takes effect on new-style API, the cropping
             could be like ((a,b),(c,d),...) so that you could be able to perform
             cropping along different edges.)
        data_format: A string, one of `channels_last` (default) or `channels_first`.
            The ordering of the dimensions in the inputs.
            `channels_last` corresponds to inputs with shape
            `(batch, ..., channels)` while `channels_first` corresponds to
            inputs with shape `(batch, channels, ...)`.
        dilation_rate: An integer or tuple/list of n integers, specifying
            the dilation rate to use for dilated convolution.
            Currently, specifying any `dilation_rate` value != 1 is
            incompatible with specifying any `strides` value != 1.
        kernel_initializer: An initializer for the convolution kernel.
        kernel_regularizer: Optional regularizer for the convolution kernel.
        kernel_constraint: Optional projection function to be applied to the
            kernel after being updated by an `Optimizer` (e.g. used to implement
            norm constraints or value constraints for layer weights). The function
            must take as input the unprojected variable and must return the
            projected variable (which must have the same shape). Constraints are
            not safe to use when doing asynchronous distributed training.
        trainable: Boolean, if `True` also add variables to the graph collection
            `GraphKeys.TRAINABLE_VARIABLES` (see `tf.Variable`).
        name: A string, the name of the layer.
    Arguments for normalization:
        normalization: The normalization type, which could be
            (1) None:  do not use normalization and do not add biases.
            (2) bias:  apply biases instead of using normalization.
            (3) batch: use batch normalization.
            (4) inst : use instance normalization.
            (5) group: use group normalization.
            If using (2), the initializer, regularizer and constraint for
            beta would be applied to the bias of convolution.
        beta_initializer: Initializer for the beta weight.
        gamma_initializer: Initializer for the gamma weight.
        beta_regularizer: Optional regularizer for the beta weight.
        gamma_regularizer: Optional regularizer for the gamma weight.
        beta_constraint: Optional constraint for the beta weight.
        gamma_constraint: Optional constraint for the gamma weight.
        groups (only for group normalization): Integer, the number of 
            groups for Group Normalization.
            Can be in the range [1, N] where N is the input dimension.
            The input dimension must be divisible by the number of groups.
    Arguments for dropout: (drop out would be only applied on the entrance
                            of conv. branch.)
        dropout: The dropout type, which could be
            (1) None:    do not use dropout.
            (2) plain:   use tf.keras.layers.Dropout.
            (3) add:     use scale-invariant addictive noise.
                         (mdnt.layers.InstanceGaussianNoise)
            (4) mul:     use multiplicative noise.
                         (tf.keras.layers.GaussianDropout)
            (5) alpha:   use alpha dropout. (tf.keras.layers.AlphaDropout)
            (6) spatial: use spatial dropout (tf.keras.layers.SpatialDropout)
        dropout_rate: The drop probability. In `add` mode, it is used as
            maximal std. To learn more, please see the docstrings of each
            method.
    Arguments for activation:
        activation: Activation function to use
            (see [activations](../activations.md)).
            If you don't specify anything, no activation is applied
            (ie. "linear" activation: `a(x) = x`).
        activity_config: keywords for the parameters of activation
            function (only for lrelu).
    Arguments (others):
        activity_regularizer: Regularizer function applied to
            the output of the layer (its "activation").
            (see [regularizer](../regularizers.md)).
    """

    def __init__(self, rank,
                 depth, ofilters,
                 kernel_size,
                 lgroups=None, lfilters=None,
                 strides=1,
                 output_mshape=None,
                 output_padding=None,
                 output_cropping=None,
                 data_format=None,
                 dilation_rate=1,
                 kernel_initializer='glorot_uniform',
                 kernel_regularizer=None,
                 kernel_constraint=None,
                 normalization='inst',
                 beta_initializer='zeros',
                 gamma_initializer='ones',
                 beta_regularizer=None,
                 gamma_regularizer=None,
                 beta_constraint=None,
                 gamma_constraint=None,
                 groups=32,
                 dropout=None,
                 dropout_rate=0.3,
                 activation=None,
                 activity_config=None,
                 activity_regularizer=None,
                 trainable=True,
                 name=None,
                 _high_activation=None,
                 **kwargs):
        if 'input_shape' not in kwargs and 'input_dim' in kwargs:
          kwargs['input_shape'] = (kwargs.pop('input_dim'),)

        super(_ResnextTranspose, self).__init__(trainable=trainable, name=name, **kwargs)
        # Inherit from keras.layers._Conv
        self.rank = rank
        self.depth = depth - 2
        self.ofilters = ofilters
        self.lgroups = lgroups
        self.lfilters = lfilters
        if self.depth < 1:
            raise ValueError('The depth of the ResNeXt block should be >= 3.')
        self.kernel_size = conv_utils.normalize_tuple(
            kernel_size, rank, 'kernel_size')
        self.strides = conv_utils.normalize_tuple(strides, rank, 'strides')
        self.output_padding = output_padding
        self.output_mshape = None
        self.output_cropping = None
        if output_mshape:
            self.output_mshape = output_mshape
        if output_cropping:
            self.output_cropping = output_cropping
        self.data_format = conv_utils.normalize_data_format(data_format)
        if rank == 1 and self.data_format == 'channels_first':
            raise ValueError('Does not support channels_first data format for 1D case due to the limitation of upsampling method.')
        self.dilation_rate = conv_utils.normalize_tuple(
            dilation_rate, rank, 'dilation_rate')
        if (not _check_dl_func(self.dilation_rate)) and (not _check_dl_func(self.strides)):
            raise ValueError('Does not support dilation_rate when strides > 1.')
        self.kernel_initializer = initializers.get(kernel_initializer)
        self.kernel_regularizer = regularizers.get(kernel_regularizer)
        self.kernel_constraint = constraints.get(kernel_constraint)
        # Inherit from mdnt.layers.normalize
        self.normalization = normalization
        if isinstance(normalization, str) and normalization in ('batch', 'inst', 'group'):
            self.gamma_initializer = initializers.get(gamma_initializer)
            self.gamma_regularizer = regularizers.get(gamma_regularizer)
            self.gamma_constraint = constraints.get(gamma_constraint)
        else:
            self.gamma_initializer = None
            self.gamma_regularizer = None
            self.gamma_constraint = None
        self.beta_initializer = initializers.get(beta_initializer)
        self.beta_regularizer = regularizers.get(beta_regularizer)
        self.beta_constraint = constraints.get(beta_constraint)
        self.groups = groups
        # Inherit from mdnt.layers.dropout
        self.dropout = dropout
        self.dropout_rate = dropout_rate
        # Inherit from keras.engine.Layer
        if _high_activation is not None:
            activation = _high_activation
        self.high_activation = _high_activation
        if isinstance(activation, str) and (activation.casefold() in ('prelu','lrelu')):
            self.activation = activations.get(None)
            self.high_activation = activation.casefold()
            self.activity_config = activity_config # dictionary passed to activation
        elif activation is not None:
            self.activation = activations.get(activation)
            self.activity_config = None
        self.sub_activity_regularizer=regularizers.get(activity_regularizer)

        # Reserve for build()
        self.channelIn = None
        
        self.trainable = trainable
        self.input_spec = InputSpec(ndim=self.rank + 2)

    def build(self, input_shape):
        input_shape = tensor_shape.TensorShape(input_shape)
        input_shape = input_shape.with_rank_at_least(self.rank + 2)
        if self.data_format == 'channels_first':
            channel_axis = 1
        else:
            channel_axis = -1
        if input_shape.dims[channel_axis].value is None:
            raise ValueError('The channel dimension of the inputs should be defined. Found `None`.')
        self.channelIn = int(input_shape[channel_axis])
        if (self.lgroups is None) or (self.lfilters is None):
            if (self.lgroups is None) and (self.lfilters is None):
                self.lgroups = 32
            if self.lfilters is None:
                cal_lfilters = self.channelIn / 2
                cal_lfilters = _cal_quad_root(a=self.depth*_get_prod(self.kernel_size)*self.lgroups, 
                               b=(self.channelIn+self.ofilters)*self.lgroups, 
                               c=-cal_lfilters*(self.channelIn+self.ofilters+self.depth*_get_prod(self.kernel_size)*cal_lfilters))
                self.lfilters = max( 1, int(round(cal_lfilters)) )
            elif self.lgroups is None:
                cal_lgroups = self.channelIn / 2
                cal_lgroups = (cal_lgroups/self.lfilters)*(self.depth*_get_prod(self.kernel_size)*cal_lgroups+self.channelIn+self.ofilters)/(self.depth*_get_prod(self.kernel_size)*self.lfilters+self.channelIn+self.ofilters)
                self.lgroups = max( 1, int(round(cal_lgroups)) )
        wholeLfilters = self.lgroups * self.lfilters
        # If setting output_mshape, need to infer output_padding & output_cropping
        if self.output_mshape is not None:
            if not isinstance(self.output_mshape, (list, tuple)):
                l_output_mshape = self.output_mshape.as_list()
            else:
                l_output_mshape = self.output_mshape
            l_output_mshape = l_output_mshape[1:-1]
            l_input_shape = input_shape.as_list()[1:-1]
            self.output_padding = []
            self.output_cropping = []
            for i in range(self.rank):
                get_shape_diff = l_output_mshape[i] - l_input_shape[i]*max(self.strides[i], self.dilation_rate[i])
                if get_shape_diff > 0:
                    b_inf = get_shape_diff // 2
                    b_sup = b_inf + get_shape_diff % 2
                    self.output_padding.append((b_inf, b_sup))
                    self.output_cropping.append((0, 0))
                elif get_shape_diff < 0:
                    get_shape_diff = -get_shape_diff
                    b_inf = get_shape_diff // 2
                    b_sup = b_inf + get_shape_diff % 2
                    self.output_cropping.append((b_inf, b_sup))
                    self.output_padding.append((0, 0))
                else:
                    self.output_cropping.append((0, 0))
                    self.output_padding.append((0, 0))
            deFlag_padding = 0
            deFlag_cropping = 0
            for i in range(self.rank):
                smp = self.output_padding[i]
                if smp[0] == 0 and smp[1] == 0:
                    deFlag_padding += 1
                smp = self.output_cropping[i]
                if smp[0] == 0 and smp[1] == 0:
                    deFlag_cropping += 1
            if deFlag_padding >= self.rank:
                self.output_padding = None
            else:
                self.output_padding = tuple(self.output_padding)
            if deFlag_cropping >= self.rank:
                self.output_cropping = None
            else:
                self.output_cropping = tuple(self.output_cropping)
        if self.rank == 1:
            self.layer_uppool = UpSampling1D(size=self.strides[0])
            self.layer_uppool.build(input_shape)
            next_shape = self.layer_uppool.compute_output_shape(input_shape)
            if self.output_padding is not None:
                self.layer_padding = ZeroPadding1D(padding=self.output_padding)[0] # Necessary for 1D case, because we need to pick (a,b) from ((a, b))
                self.layer_padding.build(next_shape)
                next_shape = self.layer_padding.compute_output_shape(next_shape)
            else:
                self.layer_padding = None
        elif self.rank == 2:
            self.layer_uppool = UpSampling2D(size=self.strides, data_format=self.data_format)
            self.layer_uppool.build(input_shape)
            next_shape = self.layer_uppool.compute_output_shape(input_shape)
            if self.output_padding is not None:
                self.layer_padding = ZeroPadding2D(padding=self.output_padding, data_format=self.data_format)
                self.layer_padding.build(next_shape)
                next_shape = self.layer_padding.compute_output_shape(next_shape)
            else:
                self.layer_padding = None
        elif self.rank == 3:
            self.layer_uppool = UpSampling3D(size=self.strides, data_format=self.data_format)
            self.layer_uppool.build(input_shape)
            next_shape = self.layer_uppool.compute_output_shape(input_shape)
            if self.output_padding is not None:
                self.layer_padding = ZeroPadding3D(padding=self.output_padding, data_format=self.data_format)
                self.layer_padding.build(next_shape)
                next_shape = self.layer_padding.compute_output_shape(next_shape)
            else:
                self.layer_padding = None
        else:
            raise ValueError('Rank of the deconvolution should be 1, 2 or 3.')
        last_use_bias = True
        if self.ofilters == self.channelIn:
            self.layer_branch_left = None
            left_shape = next_shape
        else:
            last_use_bias = False
            self.layer_branch_left = _AConv(rank = self.rank,
                          filters = self.ofilters,
                          kernel_size = 1,
                          strides = 1,
                          padding = 'same',
                          data_format = self.data_format,
                          dilation_rate = 1,
                          kernel_initializer=self.kernel_initializer,
                          kernel_regularizer=self.kernel_regularizer,
                          kernel_constraint=self.kernel_constraint,
                          normalization=self.normalization,
                          beta_initializer=self.beta_initializer,
                          gamma_initializer=self.gamma_initializer,
                          beta_regularizer=self.beta_regularizer,
                          gamma_regularizer=self.gamma_regularizer,
                          beta_constraint=self.beta_constraint,
                          gamma_constraint=self.gamma_constraint,
                          groups=self.groups,
                          activation=None,
                          activity_config=None,
                          activity_regularizer=None,
                          _high_activation=None,
                          trainable=self.trainable)
            self.layer_branch_left.build(next_shape)
            compat.collect_properties(self, self.layer_branch_left) # for compatibility
            left_shape = self.layer_branch_left.compute_output_shape(next_shape)
        # The right branch is divided into many groups
        # Right branch, with dropout
        self.layer_dropout = return_dropout(self.dropout, self.dropout_rate, axis=channel_axis, rank=self.rank)
        if self.layer_dropout is not None:
            self.layer_dropout.build(next_shape)
            right_shape = self.layer_dropout.compute_output_shape(next_shape)
        else:
            right_shape = next_shape
        self.layer_first = NACUnit(rank = self.rank,
                        filters = wholeLfilters,
                        kernel_size = 1,
                        strides = 1,
                        padding = 'same',
                        data_format = self.data_format,
                        dilation_rate = 1,
                        kernel_initializer=self.kernel_initializer,
                        kernel_regularizer=self.kernel_regularizer,
                        kernel_constraint=self.kernel_constraint,
                        normalization=self.normalization,
                        beta_initializer=self.beta_initializer,
                        gamma_initializer=self.gamma_initializer,
                        beta_regularizer=self.beta_regularizer,
                        gamma_regularizer=self.gamma_regularizer,
                        beta_constraint=self.beta_constraint,
                        gamma_constraint=self.gamma_constraint,
                        groups=self.groups,
                        activation=self.activation,
                        activity_config=self.activity_config,
                        activity_regularizer=self.sub_activity_regularizer,
                        _high_activation=self.high_activation,
                        trainable=self.trainable)
        self.layer_first.build(right_shape)
        compat.collect_properties(self, self.layer_first) # for compatibility
        right_shape = self.layer_first.compute_output_shape(right_shape)
        # Repeat blocks by depth number
        for i in range(self.depth):
            if i == 0:
                sub_dilation_rate = self.dilation_rate
            else:
                sub_dilation_rate = 1
            layer_middle = NACUnit(rank = self.rank,
                                   filters = wholeLfilters,
                                   lgroups = self.lgroups,
                                   kernel_size = self.kernel_size,
                                   strides = 1,
                                   padding = 'same',
                                   data_format = self.data_format,
                                   dilation_rate = sub_dilation_rate,
                                   kernel_initializer=self.kernel_initializer,
                                   kernel_regularizer=self.kernel_regularizer,
                                   kernel_constraint=self.kernel_constraint,
                                   normalization=self.normalization,
                                   beta_initializer=self.beta_initializer,
                                   gamma_initializer=self.gamma_initializer,
                                   beta_regularizer=self.beta_regularizer,
                                   gamma_regularizer=self.gamma_regularizer,
                                   beta_constraint=self.beta_constraint,
                                   gamma_constraint=self.gamma_constraint,
                                   groups=self.groups,
                                   activation=self.activation,
                                   activity_config=self.activity_config,
                                   activity_regularizer=self.sub_activity_regularizer,
                                   _high_activation=self.high_activation,
                                   trainable=self.trainable)
            layer_middle.build(right_shape)
            compat.collect_properties(self, layer_middle) # for compatibility
            right_shape = layer_middle.compute_output_shape(right_shape)
            setattr(self, 'layer_middle_{0:02d}'.format(i+1), layer_middle)
        self.layer_last = NACUnit(rank = self.rank,
                          filters = self.ofilters,
                          kernel_size = 1,
                          strides = 1,
                          padding = 'same',
                          data_format = self.data_format,
                          dilation_rate = 1,
                          kernel_initializer=self.kernel_initializer,
                          kernel_regularizer=self.kernel_regularizer,
                          kernel_constraint=self.kernel_constraint,
                          normalization=self.normalization,
                          beta_initializer=self.beta_initializer,
                          beta_regularizer=self.beta_regularizer,
                          beta_constraint=self.beta_constraint,
                          groups=self.groups,
                          activation=self.activation,
                          activity_config=self.activity_config,
                          activity_regularizer=self.sub_activity_regularizer,
                          _high_activation=self.high_activation,
                          _use_bias=last_use_bias,
                          trainable=self.trainable)
        self.layer_last.build(right_shape)
        compat.collect_properties(self, self.layer_last) # for compatibility
        right_shape = self.layer_last.compute_output_shape(right_shape)
        self.layer_merge = Add()
        self.layer_merge.build([left_shape, right_shape])
        next_shape = self.layer_merge.compute_output_shape([left_shape, right_shape])
        if self.output_cropping is not None:
            if self.rank == 1:
                self.layer_cropping = Cropping1D(cropping=self.output_cropping)[0]
            elif self.rank == 2:
                self.layer_cropping = Cropping2D(cropping=self.output_cropping)
            elif self.rank == 3:
                self.layer_cropping = Cropping3D(cropping=self.output_cropping)
            else:
                raise ValueError('Rank of the deconvolution should be 1, 2 or 3.')
            self.layer_cropping.build(next_shape)
            next_shape = self.layer_cropping.compute_output_shape(next_shape)
        else:
            self.layer_cropping = None
        super(_ResnextTranspose, self).build(input_shape)

    def call(self, inputs):
        outputs = self.layer_uppool(inputs)
        if self.layer_padding is not None:
            outputs = self.layer_padding(outputs)
        if self.layer_branch_left is not None:
            branch_left = self.layer_branch_left(outputs)
        else:
            branch_left = outputs
        if self.layer_dropout is not None:
            branch_right = self.layer_dropout(outputs)
        else:
            branch_right = outputs
        branch_right = self.layer_first(branch_right)
        for i in range(self.depth):
            layer_middle = getattr(self, 'layer_middle_{0:02d}'.format(i+1))
            branch_right = layer_middle(branch_right)
        branch_right = self.layer_last(branch_right)
        outputs = self.layer_merge([branch_left, branch_right])
        if self.layer_cropping is not None:
            outputs = self.layer_cropping(outputs)
        return outputs

    def compute_output_shape(self, input_shape):
        input_shape = tensor_shape.TensorShape(input_shape)
        input_shape = input_shape.with_rank_at_least(self.rank + 2)
        next_shape = self.layer_uppool.compute_output_shape(input_shape)
        if self.layer_padding is not None:
            next_shape = self.layer_padding.compute_output_shape(next_shape)
        if self.layer_branch_left is not None:
            branch_left_shape = self.layer_branch_left.compute_output_shape(next_shape)
        else:
            branch_left_shape = next_shape
        if self.layer_dropout is not None:
            branch_right_shape = self.layer_dropout.compute_output_shape(next_shape)
        else:
            branch_right_shape = next_shape
        branch_right_shape = self.layer_first.compute_output_shape(branch_right_shape)
        for i in range(self.depth):
            layer_middle = getattr(self, 'layer_middle_{0:02d}'.format(i+1))
            branch_right_shape = layer_middle.compute_output_shape(branch_right_shape)
        branch_right_shape = self.layer_last.compute_output_shape(branch_right_shape)
        next_shape = self.layer_merge.compute_output_shape([branch_left_shape, branch_right_shape])
        if self.layer_cropping is not None:
            next_shape = self.layer_cropping.compute_output_shape(next_shape)
        return next_shape
    
    def get_config(self):
        config = {
            'depth': self.depth + 2,
            'ofilters': self.ofilters,
            'lgroups': self.lgroups,
            'lfilters': self.lfilters,
            'kernel_size': self.kernel_size,
            'strides': self.strides,
            'output_mshape': self.output_mshape,
            'output_padding': self.output_padding,
            'output_cropping': self.output_cropping,
            'data_format': self.data_format,
            'dilation_rate': self.dilation_rate,
            'kernel_initializer': initializers.serialize(self.kernel_initializer),
            'kernel_regularizer': regularizers.serialize(self.kernel_regularizer),
            'kernel_constraint': constraints.serialize(self.kernel_constraint),
            'normalization': self.normalization,
            'beta_initializer': initializers.serialize(self.beta_initializer),
            'gamma_initializer': initializers.serialize(self.gamma_initializer),
            'beta_regularizer': regularizers.serialize(self.beta_regularizer),
            'gamma_regularizer': regularizers.serialize(self.gamma_regularizer),
            'beta_constraint': constraints.serialize(self.beta_constraint),
            'gamma_constraint': constraints.serialize(self.gamma_constraint),
            'groups': self.groups,
            'dropout': self.dropout,
            'dropout_rate': self.dropout_rate,
            'activation': activations.serialize(self.activation),
            'activity_config': self.activity_config,
            'activity_regularizer': regularizers.serialize(self.activity_regularizer),
            '_high_activation': self.high_activation
        }
        base_config = super(_ResnextTranspose, self).get_config()
        return dict(list(base_config.items()) + list(config.items()))
        
class Resnext1DTranspose(_ResnextTranspose):
    """Modern transposed ResNeXt layer (sometimes called ResNeXt deconvolution).
    `Resnext1DTranspose` implements the operation:
        `output = AConv1D(Upsamp(input)) + Conv1D(Actv(Norm(AGPConv1D(AGPConv1D( ... )))))`
    In some cases, the first term may not need to be convoluted.
    The transposed ResNeXt block is realized by simply adding upsamping on the in-
    put layer, because previous works show that the transposed convolution is eqi-
    valent to upsampling + convolution.
    Arguments for residual block:
        depth: An integer, indicates the repentance of convolutional blocks.
        ofilters: Integer, the dimensionality of the output space (i.e. the number
            of filters of output).
        lgroups: Integer, the group number of the latent convolution branch. The
            number of filters in the whole latent space is lgroups * lfilters.
        lfilters: Integer, the dimensionality in each the lattent group (i.e. the
            number of filters in each latent convolution branch).
    Arguments for convolution:
        kernel_size: An integer or tuple/list of n integers, specifying the
            length of the convolution window.
        strides: An integer or tuple/list of n integers,
            specifying the stride length of the convolution.
            Specifying any stride value != 1 is incompatible with specifying
            any `dilation_rate` value != 1.
        output_mshape: (Only avaliable for new-style API) An integer or tuple/list
            of the desired output shape. When setting this option, `output_padding`
            and `out_cropping` would be inferred from the input shape, which means
            users' options would be invalid for the following two options.
            A recommended method of using this method is applying such a scheme:
                `AConv(..., output_mshape=tensor.get_shape())`
        output_padding: An integer or tuple/list of n integers,
            specifying the amount of padding along the height and width
            of the output tensor.
            The amount of output padding along a given dimension must be
            lower than the stride along that same dimension.
            If set to `None` (default), the output shape would not be padded.
        out_cropping: (Only avaliable for new-style API) An integer or tuple/list 
            of n integers, specifying the amount of cropping along the axes of the
            output tensor. The amount of output cropping along a given dimension must
            be lower than the stride along that same dimension.
            If set to `None` (default), the output shape would not be cropped.
        data_format: A string, only support `channels_last` here:
            `channels_last` corresponds to inputs with shape
            `(batch, steps channels)`
        dilation_rate: An integer or tuple/list of n integers, specifying
            the dilation rate to use for dilated convolution.
            Currently, specifying any `dilation_rate` value != 1 is
            incompatible with specifying any `strides` value != 1.
        kernel_initializer: An initializer for the convolution kernel.
        kernel_regularizer: Optional regularizer for the convolution kernel.
        kernel_constraint: Optional projection function to be applied to the
            kernel after being updated by an `Optimizer` (e.g. used to implement
            norm constraints or value constraints for layer weights). The function
            must take as input the unprojected variable and must return the
            projected variable (which must have the same shape). Constraints are
            not safe to use when doing asynchronous distributed training.
        trainable: Boolean, if `True` also add variables to the graph collection
            `GraphKeys.TRAINABLE_VARIABLES` (see `tf.Variable`).
        name: A string, the name of the layer.
    Arguments for normalization:
        normalization: The normalization type, which could be
            (1) None:  do not use normalization and do not add biases.
            (2) bias:  apply biases instead of using normalization.
            (3) batch: use batch normalization.
            (4) inst : use instance normalization.
            (5) group: use group normalization.
            If using (2), the initializer, regularizer and constraint for
            beta would be applied to the bias of convolution.
        beta_initializer: Initializer for the beta weight.
        gamma_initializer: Initializer for the gamma weight.
        beta_regularizer: Optional regularizer for the beta weight.
        gamma_regularizer: Optional regularizer for the gamma weight.
        beta_constraint: Optional constraint for the beta weight.
        gamma_constraint: Optional constraint for the gamma weight.
        groups (only for group normalization): Integer, the number of 
            groups for Group Normalization.
            Can be in the range [1, N] where N is the input dimension.
            The input dimension must be divisible by the number of groups.
    Arguments for dropout: (drop out would be only applied on the entrance
                            of conv. branch.)
        dropout: The dropout type, which could be
            (1) None:    do not use dropout.
            (2) plain:   use tf.keras.layers.Dropout.
            (3) add:     use scale-invariant addictive noise.
                         (mdnt.layers.InstanceGaussianNoise)
            (4) mul:     use multiplicative noise.
                         (tf.keras.layers.GaussianDropout)
            (5) alpha:   use alpha dropout. (tf.keras.layers.AlphaDropout)
            (6) spatial: use spatial dropout (tf.keras.layers.SpatialDropout)
        dropout_rate: The drop probability. In `add` mode, it is used as
            maximal std. To learn more, please see the docstrings of each
            method.
    Arguments for activation:
        activation: Activation function to use
            (see [activations](../activations.md)).
            If you don't specify anything, no activation is applied
            (ie. "linear" activation: `a(x) = x`).
        activity_config: keywords for the parameters of activation
            function (only for lrelu).
    Arguments (others):
        activity_regularizer: Regularizer function applied to
            the output of the layer (its "activation").
            (see [regularizer](../regularizers.md)).
    Input shape:
        3D tensor with shape: `(batch_size, steps, input_dim)`
    Output shape:
        3D tensor with shape: `(batch_size, new_steps, filters)`
        `steps` value might have changed due to padding or strides.
    """

    def __init__(self, ofilters,
                 kernel_size,
                 lgroups=None, lfilters=None,
                 depth=3,
                 strides=1,
                 output_mshape=None,
                 output_padding=None,
                 output_cropping=None,
                 data_format=None,
                 dilation_rate=1,
                 kernel_initializer='glorot_uniform',
                 kernel_regularizer=None,
                 kernel_constraint=None,
                 normalization='inst',
                 beta_initializer='zeros',
                 gamma_initializer='ones',
                 beta_regularizer=None,
                 gamma_regularizer=None,
                 beta_constraint=None,
                 gamma_constraint=None,
                 groups=32,
                 dropout=None,
                 dropout_rate=0.3,
                 activation=None,
                 activity_config=None,
                 activity_regularizer=None,
                 **kwargs):
        super(Resnext1DTranspose, self).__init__(
            rank=1, depth=depth, ofilters=ofilters,
            kernel_size=kernel_size,
            lgroups=lgroups, lfilters=lfilters,
            strides=strides,
            output_mshape=output_mshape,
            output_padding=output_padding,
            output_cropping=output_cropping,
            data_format=data_format,
            dilation_rate=dilation_rate,
            kernel_initializer=initializers.get(kernel_initializer),
            kernel_regularizer=regularizers.get(kernel_regularizer),
            kernel_constraint=constraints.get(kernel_constraint),
            normalization=normalization,
            beta_initializer=initializers.get(beta_initializer),
            gamma_initializer=initializers.get(gamma_initializer),
            beta_regularizer=regularizers.get(beta_regularizer),
            gamma_regularizer=regularizers.get(gamma_regularizer),
            beta_constraint=constraints.get(beta_constraint),
            gamma_constraint=constraints.get(gamma_constraint),
            groups=groups,
            dropout=dropout,
            dropout_rate=dropout_rate,
            activation=activation,
            activity_config=activity_config,
            activity_regularizer=regularizers.get(activity_regularizer),
            **kwargs)
            
class Resnext2DTranspose(_ResnextTranspose):
    """Modern transposed ResNeXt layer (sometimes called ResNeXt deconvolution).
    `Resnext2DTranspose` implements the operation:
        `output = AConv2D(Upsamp(input)) + Conv2D(Actv(Norm(AGPConv2D(AGPConv2D( ... )))))`
    In some cases, the first term may not need to be convoluted.
    The transposed ResNeXt block is realized by simply adding upsamping on the in-
    put layer, because previous works show that the transposed convolution is eqi-
    valent to upsampling + convolution.
    Arguments for residual block:
        depth: An integer, indicates the repentance of convolutional blocks.
        ofilters: Integer, the dimensionality of the output space (i.e. the number
            of filters of output).
        lgroups: Integer, the group number of the latent convolution branch. The
            number of filters in the whole latent space is lgroups * lfilters.
        lfilters: Integer, the dimensionality in each the lattent group (i.e. the
            number of filters in each latent convolution branch).
    Arguments for convolution:
        kernel_size: An integer or tuple/list of 2 integers, specifying the
            height and width of the 2D convolution window.
            Can be a single integer to specify the same value for
            all spatial dimensions.
        strides: An integer or tuple/list of 2 integers,
            specifying the strides of the convolution along the height and width.
            Can be a single integer to specify the same value for
            all spatial dimensions.
            Specifying any stride value != 1 is incompatible with specifying
            any `dilation_rate` value != 1.
        output_mshape: (Only avaliable for new-style API) An integer or tuple/list
            of the desired output shape. When setting this option, `output_padding`
            and `out_cropping` would be inferred from the input shape, which means
            users' options would be invalid for the following two options.
            A recommended method of using this method is applying such a scheme:
                `AConv(..., output_mshape=tensor.get_shape())`
        output_padding: An integer or tuple/list of 2 integers,
            specifying the amount of padding along the height and width
            of the output tensor.
            Can be a single integer to specify the same value for all
            spatial dimensions.
            The amount of output padding along a given dimension must be
            lower than the stride along that same dimension.
            If set to `None` (default), the output shape would not be padded.
        out_cropping: (Only avaliable for new-style API) An integer or tuple/list 
            of n integers, specifying the amount of cropping along the axes of the
            output tensor. The amount of output cropping along a given dimension must
            be lower than the stride along that same dimension.
            If set to `None` (default), the output shape would not be cropped.
        data_format: A string,
            one of `channels_last` (default) or `channels_first`.
            The ordering of the dimensions in the inputs.
            `channels_last` corresponds to inputs with shape
            `(batch, height, width, channels)` while `channels_first`
            corresponds to inputs with shape
            `(batch, channels, height, width)`.
            It defaults to the `image_data_format` value found in your
            Keras config file at `~/.keras/keras.json`.
            If you never set it, then it will be "channels_last".
        dilation_rate: an integer or tuple/list of 2 integers, specifying
            the dilation rate to use for dilated convolution.
            Can be a single integer to specify the same value for
            all spatial dimensions.
            Currently, specifying any `dilation_rate` value != 1 is
            incompatible with specifying any stride value != 1.
        kernel_initializer: An initializer for the convolution kernel.
        kernel_regularizer: Optional regularizer for the convolution kernel.
        kernel_constraint: Optional projection function to be applied to the
            kernel after being updated by an `Optimizer` (e.g. used to implement
            norm constraints or value constraints for layer weights). The function
            must take as input the unprojected variable and must return the
            projected variable (which must have the same shape). Constraints are
            not safe to use when doing asynchronous distributed training.
        trainable: Boolean, if `True` also add variables to the graph collection
            `GraphKeys.TRAINABLE_VARIABLES` (see `tf.Variable`).
        name: A string, the name of the layer.
    Arguments for normalization:
        normalization: The normalization type, which could be
            (1) None:  do not use normalization and do not add biases.
            (2) bias:  apply biases instead of using normalization.
            (3) batch: use batch normalization.
            (4) inst : use instance normalization.
            (5) group: use group normalization.
            If using (2), the initializer, regularizer and constraint for
            beta would be applied to the bias of convolution.
        beta_initializer: Initializer for the beta weight.
        gamma_initializer: Initializer for the gamma weight.
        beta_regularizer: Optional regularizer for the beta weight.
        gamma_regularizer: Optional regularizer for the gamma weight.
        beta_constraint: Optional constraint for the beta weight.
        gamma_constraint: Optional constraint for the gamma weight.
        groups (only for group normalization): Integer, the number of 
            groups for Group Normalization.
            Can be in the range [1, N] where N is the input dimension.
            The input dimension must be divisible by the number of groups.
    Arguments for dropout: (drop out would be only applied on the entrance
                            of conv. branch.)
        dropout: The dropout type, which could be
            (1) None:    do not use dropout.
            (2) plain:   use tf.keras.layers.Dropout.
            (3) add:     use scale-invariant addictive noise.
                         (mdnt.layers.InstanceGaussianNoise)
            (4) mul:     use multiplicative noise.
                         (tf.keras.layers.GaussianDropout)
            (5) alpha:   use alpha dropout. (tf.keras.layers.AlphaDropout)
            (6) spatial: use spatial dropout (tf.keras.layers.SpatialDropout)
        dropout_rate: The drop probability. In `add` mode, it is used as
            maximal std. To learn more, please see the docstrings of each
            method.
    Arguments for activation:
        activation: Activation function to use
            (see [activations](../activations.md)).
            If you don't specify anything, no activation is applied
            (ie. "linear" activation: `a(x) = x`).
        activity_config: keywords for the parameters of activation
            function (only for lrelu).
    Arguments (others):
        activity_regularizer: Regularizer function applied to
            the output of the layer (its "activation").
            (see [regularizer](../regularizers.md)).
    Input shape:
        4D tensor with shape:
        `(batch, channels, rows, cols)` if data_format='channels_first'
        or 4D tensor with shape:
        `(batch, rows, cols, channels)` if data_format='channels_last'.
    Output shape:
        4D tensor with shape:
        `(batch, filters, new_rows, new_cols)` if data_format='channels_first'
        or 4D tensor with shape:
        `(batch, new_rows, new_cols, filters)` if data_format='channels_last'.
        `rows` and `cols` values might have changed due to padding.
    """

    def __init__(self, ofilters,
                 kernel_size,
                 lgroups=None, lfilters=None,
                 depth=3,
                 strides=(1, 1),
                 output_mshape=None,
                 output_padding=None,
                 output_cropping=None,
                 data_format=None,
                 dilation_rate=(1, 1),
                 kernel_initializer='glorot_uniform',
                 kernel_regularizer=None,
                 kernel_constraint=None,
                 normalization='inst',
                 beta_initializer='zeros',
                 gamma_initializer='ones',
                 beta_regularizer=None,
                 gamma_regularizer=None,
                 beta_constraint=None,
                 gamma_constraint=None,
                 groups=32,
                 dropout=None,
                 dropout_rate=0.3,
                 activation=None,
                 activity_config=None,
                 activity_regularizer=None,
                 **kwargs):
        super(Resnext2DTranspose, self).__init__(
            rank=2, depth=depth, ofilters=ofilters,
            kernel_size=kernel_size,
            lgroups=lgroups, lfilters=lfilters,
            strides=strides,
            output_mshape=output_mshape,
            output_padding=output_padding,
            output_cropping=output_cropping,
            data_format=data_format,
            dilation_rate=dilation_rate,
            kernel_initializer=initializers.get(kernel_initializer),
            kernel_regularizer=regularizers.get(kernel_regularizer),
            kernel_constraint=constraints.get(kernel_constraint),
            normalization=normalization,
            beta_initializer=initializers.get(beta_initializer),
            gamma_initializer=initializers.get(gamma_initializer),
            beta_regularizer=regularizers.get(beta_regularizer),
            gamma_regularizer=regularizers.get(gamma_regularizer),
            beta_constraint=constraints.get(beta_constraint),
            gamma_constraint=constraints.get(gamma_constraint),
            groups=groups,
            dropout=dropout,
            dropout_rate=dropout_rate,
            activation=activation,
            activity_config=activity_config,
            activity_regularizer=regularizers.get(activity_regularizer),
            **kwargs)
            
class Resnext3DTranspose(_ResnextTranspose):
    """Modern transposed ResNeXt layer (sometimes called ResNeXt deconvolution).
    `Resnext3DTranspose` implements the operation:
        `output = AConv3D(Upsamp(input)) + Conv3D(Actv(Norm(AGPConv3D(AGPConv3D( ... )))))`
    In some cases, the first term may not need to be convoluted.
    The transposed ResNeXt block is realized by simply adding upsamping on the in-
    put layer, because previous works show that the transposed convolution is eqi-
    valent to upsampling + convolution.
    Arguments for residual block:
        depth: An integer, indicates the repentance of convolutional blocks.
        ofilters: Integer, the dimensionality of the output space (i.e. the number
            of filters of output).
        lgroups: Integer, the group number of the latent convolution branch. The
            number of filters in the whole latent space is lgroups * lfilters.
        lfilters: Integer, the dimensionality in each the lattent group (i.e. the
            number of filters in each latent convolution branch).
    Arguments for convolution:
        kernel_size: An integer or tuple/list of 3 integers, specifying the
            depth, height and width of the 3D convolution window.
            Can be a single integer to specify the same value for
            all spatial dimensions.
        strides: An integer or tuple/list of 3 integers,
            specifying the strides of the convolution along the depth, height
            and width.
            Can be a single integer to specify the same value for
            all spatial dimensions.
            Specifying any stride value != 1 is incompatible with specifying
            any `dilation_rate` value != 1.
        output_mshape: (Only avaliable for new-style API) An integer or tuple/list
            of the desired output shape. When setting this option, `output_padding`
            and `out_cropping` would be inferred from the input shape, which means
            users' options would be invalid for the following two options.
            A recommended method of using this method is applying such a scheme:
                `AConv(..., output_mshape=tensor.get_shape())`
        output_padding: An integer or tuple/list of 3 integers,
            specifying the amount of padding along the depth, height, and
            width.
            Can be a single integer to specify the same value for all
            spatial dimensions.
            The amount of output padding along a given dimension must be
            lower than the stride along that same dimension.
            If set to `None` (default), the output shape is inferred.
        out_cropping: (Only avaliable for new-style API) An integer or tuple/list 
            of n integers, specifying the amount of cropping along the axes of the
            output tensor. The amount of output cropping along a given dimension must
            be lower than the stride along that same dimension.
            If set to `None` (default), the output shape would not be cropped.
        data_format: A string,
            one of `channels_last` (default) or `channels_first`.
            The ordering of the dimensions in the inputs.
            `channels_last` corresponds to inputs with shape
            `(batch, depth, height, width, channels)` while `channels_first`
            corresponds to inputs with shape
            `(batch, channels, depth, height, width)`.
            It defaults to the `image_data_format` value found in your
            Keras config file at `~/.keras/keras.json`.
            If you never set it, then it will be "channels_last".
        dilation_rate: an integer or tuple/list of 3 integers, specifying
            the dilation rate to use for dilated convolution.
            Can be a single integer to specify the same value for
            all spatial dimensions.
            Currently, specifying any `dilation_rate` value != 1 is
            incompatible with specifying any stride value != 1.
        kernel_initializer: An initializer for the convolution kernel.
        kernel_regularizer: Optional regularizer for the convolution kernel.
        kernel_constraint: Optional projection function to be applied to the
            kernel after being updated by an `Optimizer` (e.g. used to implement
            norm constraints or value constraints for layer weights). The function
            must take as input the unprojected variable and must return the
            projected variable (which must have the same shape). Constraints are
            not safe to use when doing asynchronous distributed training.
        trainable: Boolean, if `True` also add variables to the graph collection
            `GraphKeys.TRAINABLE_VARIABLES` (see `tf.Variable`).
        name: A string, the name of the layer.
    Arguments for normalization:
        normalization: The normalization type, which could be
            (1) None:  do not use normalization and do not add biases.
            (2) bias:  apply biases instead of using normalization.
            (3) batch: use batch normalization.
            (4) inst : use instance normalization.
            (5) group: use group normalization.
            If using (2), the initializer, regularizer and constraint for
            beta would be applied to the bias of convolution.
        beta_initializer: Initializer for the beta weight.
        gamma_initializer: Initializer for the gamma weight.
        beta_regularizer: Optional regularizer for the beta weight.
        gamma_regularizer: Optional regularizer for the gamma weight.
        beta_constraint: Optional constraint for the beta weight.
        gamma_constraint: Optional constraint for the gamma weight.
        groups (only for group normalization): Integer, the number of 
            groups for Group Normalization.
            Can be in the range [1, N] where N is the input dimension.
            The input dimension must be divisible by the number of groups.
    Arguments for dropout: (drop out would be only applied on the entrance
                            of conv. branch.)
        dropout: The dropout type, which could be
            (1) None:    do not use dropout.
            (2) plain:   use tf.keras.layers.Dropout.
            (3) add:     use scale-invariant addictive noise.
                         (mdnt.layers.InstanceGaussianNoise)
            (4) mul:     use multiplicative noise.
                         (tf.keras.layers.GaussianDropout)
            (5) alpha:   use alpha dropout. (tf.keras.layers.AlphaDropout)
            (6) spatial: use spatial dropout (tf.keras.layers.SpatialDropout)
        dropout_rate: The drop probability. In `add` mode, it is used as
            maximal std. To learn more, please see the docstrings of each
            method.
    Arguments for activation:
        activation: Activation function to use
            (see [activations](../activations.md)).
            If you don't specify anything, no activation is applied
            (ie. "linear" activation: `a(x) = x`).
        activity_config: keywords for the parameters of activation
            function (only for lrelu).
    Arguments (others):
        activity_regularizer: Regularizer function applied to
            the output of the layer (its "activation").
            (see [regularizer](../regularizers.md)).
    Input shape:
        5D tensor with shape:
        `(batch, channels, depth, rows, cols)` if data_format='channels_first'
        or 5D tensor with shape:
        `(batch, depth, rows, cols, channels)` if data_format='channels_last'.
    Output shape:
        5D tensor with shape:
        `(batch, filters, new_depth, new_rows, new_cols)` if
        data_format='channels_first'
        or 5D tensor with shape:
        `(batch, new_depth, new_rows, new_cols, filters)` if
        data_format='channels_last'.
        `depth` and `rows` and `cols` values might have changed due to padding.
    """

    def __init__(self, ofilters,
                 kernel_size,
                 lgroups=None, lfilters=None,
                 depth=3,
                 strides=(1, 1, 1),
                 output_mshape=None,
                 output_padding=None,
                 output_cropping=None,
                 data_format=None,
                 dilation_rate=(1, 1, 1),
                 kernel_initializer='glorot_uniform',
                 kernel_regularizer=None,
                 kernel_constraint=None,
                 normalization='inst',
                 beta_initializer='zeros',
                 gamma_initializer='ones',
                 beta_regularizer=None,
                 gamma_regularizer=None,
                 beta_constraint=None,
                 gamma_constraint=None,
                 groups=32,
                 dropout=None,
                 dropout_rate=0.3,
                 activation=None,
                 activity_config=None,
                 activity_regularizer=None,
                 **kwargs):
        super(Resnext3DTranspose, self).__init__(
            rank=3, depth=depth, ofilters=ofilters,
            kernel_size=kernel_size,
            lgroups=lgroups, lfilters=lfilters,
            strides=strides,
            output_mshape=output_mshape,
            output_padding=output_padding,
            output_cropping=output_cropping,
            data_format=data_format,
            dilation_rate=dilation_rate,
            kernel_initializer=initializers.get(kernel_initializer),
            kernel_regularizer=regularizers.get(kernel_regularizer),
            kernel_constraint=constraints.get(kernel_constraint),
            normalization=normalization,
            beta_initializer=initializers.get(beta_initializer),
            gamma_initializer=initializers.get(gamma_initializer),
            beta_regularizer=regularizers.get(beta_regularizer),
            gamma_regularizer=regularizers.get(gamma_regularizer),
            beta_constraint=constraints.get(beta_constraint),
            gamma_constraint=constraints.get(gamma_constraint),
            groups=groups,
            dropout=dropout,
            dropout_rate=dropout_rate,
            activation=activation,
            activity_config=activity_config,
            activity_regularizer=regularizers.get(activity_regularizer),
            **kwargs)