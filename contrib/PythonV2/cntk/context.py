# Copyright (c) Microsoft. All rights reserved.

# Licensed under the MIT license. See LICENSE.md file in the project root
# for full license information.
# ==============================================================================

# TODO: Settle on a centralized location for all the documentation that is in docstrings
# TODO: Take out the saved model from the context

from abc import ABCMeta, abstractmethod
import os
import re
import numpy as np

from .utils import with_metaclass
from .utils import create_ValuePtr_from_NumPy, create_ValuePtr_for_Variable, is_tensor, is_tensor_list, sanitize_batch
#from .reader import InputMap

from . import cntk_py

# TODO: add validate method
# TODO: overload action methods to support numpy matrices as inputs
# TODO: overload action methods to have versions that do not need reader
# TODO: clean_up should become a property of train()
# or numpy inputs

_CONTEXT = {}


def get_context(handle=None):
    '''
    If the context for the current handle is already built it returns it. Otherwise,
    it will build new context and return it.
    Args:
        handle (str): context name
    Returns:
        :class:`cntk.context.LocalExecutionContext`
    '''    
    if handle is None:
        handle = 'default'
    
    if handle not in _CONTEXT:
        _CONTEXT[handle] = LocalExecutionContext(handle)

    return _CONTEXT[handle]


def get_new_context():
    while True:
        new_handle = str(np.random.random())[2:]
        if new_handle not in _CONTEXT:
            return get_context(new_handle)


class AbstractContext(with_metaclass(ABCMeta, object)):

    '''
    This is the abstract CNTK context. It provides an API to run CNTK actions.

    Args:
        name (str): context name
        device_id (int): whether to use CPU (-1) or GPU if `device_id>=0`, in which case it denotes the GPU index
        precision (str): either float or double
    '''

    def __init__(self, name,
                 device_id=-1,
                 precision="float"):
        if isinstance(name, str):
            tmpdir = name
        else:
            tmpdir = id(name)

        self.directory = os.path.abspath('_cntk_%s' % tmpdir)

        ''' do this only when model needs to be written
        if os.path.exists(self.directory):
            print("Directory '%s' already exists" %
                  self.directory)
        else:
            os.mkdir(self.directory)
            '''

        self.name = name
        self.device_id = device_id
        self.precision = precision
        self.input_nodes = set()

    @property
    def precision(self):
        return self._precision

    @precision.setter
    def precision(self, val):
        if val not in ('float', 'double'):
            raise ValueError('type "%s" is not supported'%val)
        self._precision = val

    @property
    def precision_numpy(self):
        if self.precision == 'float':
            return np.float32
        else:
            return np.float64

    @abstractmethod
    def train(self, root_nodes, training_params, input_map=None, override_existing=True):
        '''
        Abstract method to run the train action locally.

        Args:
            root_nodes (:class:`cntk.graph.ComputationNode` or list thereof): node(s) to start the graph generation from (most likely evaluation and criterion nodes)
            training_params (instance of :class:`cntk.sgd.SGDParams`): the SGD training parameters to use for training
            node (:class:`cntk.graph.ComputationNode`): the node to evaluate
            input_map (dict): map from input nodes to :class:`cntk.reader.InputMap`
            override_existing (bool): if the folder exists already override it

        Returns:
            the console output generated by the CNTK training run
        '''
        pass

    @abstractmethod
    def test(self, root_nodes=None, input_map=None):
        '''
        Abstract method for the action test.

        Args:
            root_nodes (:class:`cntk.graph.ComputationNode` or list thereof): node(s) to start the graph generation from (most likely evaluation and criterion nodes)
            input_map (:class:`cntk.reader.InputMap`): describes how to map inputs to the data in a data file using a reader

        Returns:
            dictionary containing `SamplesSeen`, `Perplexity`, and values for
            objective and evaluation error indexed by their node names
        '''
        pass

    @abstractmethod
    def write(self, input_map=None):
        '''
        Abstract method for the action write. It evaluates the trained model on 
        the data provided by the reader.

        Args:
            node (:class:`cntk.graph.ComputationNode`): the node to evaluate.
            input_map (:class:`cntk.reader.InputMap`): describes how to map inputs to the data in a data file using a reader

        Returns: 
            output generated by `node`
        '''
        pass

    @abstractmethod
    def eval(self, node, input_map=None, backward_pass=False, input_name=None):
        '''
        Abstract method for the action write.  It evaluates `node` on the data
        provided by the reader. This is useful mainly to explore the operators
        and for convenient unit testing.
        
        Args:
            node (:class:`cntk.graph.ComputationNode`): the node to evaluate.
            input_map (:class:`cntk.reader.InputMap`): describes how to map inputs to the data in a data file using a reader
            backward_pass (bool): set to `True` if you want to output the gradient of a node (backward pass)
            input_name (:class:`cntk.graph.ComputationNode`): if `backward_pass` is `True` then `input_node` should contain the input name that the gradient is performed with respect to.

        Returns: 
            output generated by `node`
        '''
        pass



class LocalExecutionContext(AbstractContext):

    '''
    This is a sub-class of AbstractContext, use it to run CNTK locally.
        
    Args:
        name (str): context name
        device_id (int): whether to use CPU (-1) or GPU if `device_id>=0`, in which case it denotes the GPU index
        precision (str): either float or double
        clean_up (bool): whether the temporary directory should be removed when the context is left        
    '''

    def __init__(self, name,
                 device_id=-1,
                 precision="float",
                 clean_up=True):
        super(self.__class__,self).__init__(name, device_id, precision)
        self.clean_up = clean_up
        self.model_dir = os.path.join(self.directory, 'Models')
        self.model_path = os.path.join(self.model_dir, self.name)

        if device_id==-1:
            self.device = cntk_py.DeviceDescriptor_CPUDevice()
        else:
            self.device = cntk_py.DeviceDescriptor_GPUDevice(device_id)

    def __enter__(self):
        _CONTEXT[self.name] = self
        return self

    def __exit__(self, exc_type, exc_value, exc_tb):
        del _CONTEXT[self.name]
        
    def train(self, root_nodes, training_params, input_map=None, override_existing=True):
        '''
        Run the train action locally.

        Args:
            root_nodes (:class:`cntk.graph.ComputationNode` or list thereof): node(s) to start the graph generation from (most likely evaluation and criterion nodes)
            training_params (instance of :class:`cntk.sgd.SGDParams`): the SGD training parameters to use for training
            node (:class:`cntk.graph.ComputationNode`): the node to evaluate
            input_map (:class:`cntk.reader.InputMap`): describes how to map inputs to the data in a data file using a reader
            override_existing (bool): if the folder exists already override it

        Returns:
            the console output generated by the CNTK training run
        '''
        raise NotImplemented

    def test(self, root_nodes=None, input_map=None):
        '''
        Run the test action locally.

        Args:
            root_nodes (:class:`cntk.graph.ComputationNode` or list thereof): node(s) to start the graph generation from (most likely evaluation and criterion nodes)
            input_map (:class:`cntk.reader.InputMap`): describes how to map inputs to the data in a data file using a reader

        Returns:
            dictionary containing `SamplesSeen`, `Perplexity`, and values for
            objective and evaluation error indexed by their node names
        '''
        raise NotImplemented

    def write(self, input_map=None):
        '''
        It evaluates the trained model on the data provided by the reader.

        Args:
            node (:class:`cntk.graph.ComputationNode`): the node to evaluate.
            input_map (:class:`cntk.reader.InputMap`): describes how to map inputs to the data in a data file using a reader

        Returns: 
            output generated by `node`
        '''
        raise NotImplemented

    def eval(self, op, input_map=None, backward_pass=False, input_name=None):
        '''
        It evaluates `op` on the data provided by the reader. This is useful
        mainly to explore the operators and for convenient unit testing. 
        
        Args:
            op (:class:`Function`): operation to evaluate
            input_map (:class:`cntk.reader.InputMap`): describes how to map inputs to the data in a data file using a number, NumPy array or reader object
            backward_pass (bool): set to `True` if you want to output the gradient of a variable (backward pass)
            input_name (:class:`cntk.variables.Variable`): if `backward_pass` is `True` then `input_node` should contain the input name that the gradient is performed with respect to.

        Returns: 
            output generated by `op`. If `op` is an iterable, also an iterable is returned.
        '''
        if backward_pass:
            if input_name is None:
                raise ValueError(
                    'an input name is required when backward pass is enabled')

        if backward_pass:
            raise ValueError('not yet supported')


        var_map = cntk_py.MapVarValuePtr()
        if input_map:
            for var, val in input_map.items():
                if isinstance(val, np.ndarray):
                    if val.dtype not in (np.float32, np.float64):
                        raise ValueError('only float32 and float64 are supported')
                    val = sanitize_batch(val, self.precision_numpy, self.device)
                else:
                    if is_tensor(val):
                        val = np.asarray(val, dtype=self.precision_numpy)
                        val = create_ValuePtr_from_NumPy(val, self.device)
                    elif is_tensor_list(val):
                        val = sanitize_batch(val, self.precision_numpy, self.device)
                    else:
                        raise ValueError('values of input_map need to be NumPy arrays, lists of lists or list of NumPy arrays, but you gave a "%s"'%type(val))

                var_map[var] = val


        outputs =  cntk_py.MapVarValuePtr()
        outputs_retain =  cntk_py.VarSet()
        for v in op.Outputs():
            outputs[v] = create_ValuePtr_for_Variable(v)
            outputs_retain.add(v)

        state = op.ForwardMap(var_map, outputs, self.device, outputs_retain)
        if backward_pass:
            op.BackwardMap(state, var_map, outputs, self.device)

        all_outputs_data = {}
        for v in op.Outputs():
            all_outputs_data[v] = outputs[v].Data().ToNumPy() 

        print(all_outputs_data)
        if len(all_outputs_data)==1:
            for val in all_outputs_data.values():
                return val

        return all_outputs_data

