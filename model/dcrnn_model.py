from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import tensorflow as tf

from tensorflow.contrib import legacy_seq2seq

from lib.metrics import masked_mse_loss, masked_mae_loss, masked_rmse_loss
from model.dcrnn_cell import DCGRUCell,DCIndCell
from model.tf_model import TFModel


class DCRNNModel(TFModel):
    def __init__(self, is_training, config, scaler=None, adj_mx=None):
        super(DCRNNModel, self).__init__(config, scaler=scaler)
        batch_size = int(config.get('batch_size'))
        max_diffusion_step = int(config.get('max_diffusion_step', 2))
        cl_decay_steps = int(config.get('cl_decay_steps', 1000))
        filter_type = config.get('filter_type', 'laplacian')
        horizon = int(config.get('horizon', 1))
        input_dim = int(config.get('input_dim', 1))
        loss_func = config.get('loss_func', 'MSE')
        max_grad_norm = float(config.get('max_grad_norm', 5.0))
        num_nodes = int(config.get('num_nodes', 1))
        num_rnn_layers = int(config.get('num_rnn_layers', 1))
        output_dim = int(config.get('output_dim', 1))
        rnn_units = int(config.get('rnn_units'))
        seq_len = int(config.get('seq_len'))
        use_curriculum_learning = bool(config.get('use_curriculum_learning', False))

        assert input_dim == output_dim, 'input_dim: %d != output_dim: %d' % (input_dim, output_dim)
        # Input (batch_size, timesteps, num_sensor, input_dim)
        self._inputs = tf.placeholder(tf.float32, shape=(batch_size, seq_len, num_nodes, input_dim), name='inputs')
        # Labels: (batch_size, timesteps, num_sensor, output_dim)
        self._labels = tf.placeholder(tf.float32, shape=(batch_size, horizon, num_nodes, output_dim), name='labels')
        GO_SYMBOL = tf.zeros(shape=(batch_size, num_nodes * input_dim))

#        cell = DCIndCell(rnn_units, adj_mx, max_diffusion_step=max_diffusion_step, num_nodes=num_nodes,
#                         filter_type=filter_type)
#        cell_with_projection = DCIndCell(rnn_units, adj_mx, max_diffusion_step=max_diffusion_step, num_nodes=num_nodes,
#                                         num_proj=output_dim, filter_type=filter_type)
#        encoding_cells = [cell] * num_rnn_layers
#        decoding_cells = [cell] * (num_rnn_layers - 1) + [cell_with_projection]
        
        encoding_cells = []
        for i in range(num_rnn_layers):
            encoding_cells.append(DCIndCell(rnn_units, adj_mx, max_diffusion_step=max_diffusion_step, num_nodes=num_nodes,
                         filter_type=filter_type))
        decoding_cells = []
        for i in range(num_rnn_layers - 1):
            decoding_cells.append(DCIndCell(rnn_units, adj_mx, max_diffusion_step=max_diffusion_step, num_nodes=num_nodes,
                         filter_type=filter_type))
        decoding_cells.append(DCIndCell(rnn_units, adj_mx, max_diffusion_step=max_diffusion_step, num_nodes=num_nodes,
                                         num_proj=output_dim, filter_type=filter_type))
        
        encoding_cells = tf.contrib.rnn.MultiRNNCell(encoding_cells, state_is_tuple=True)
        decoding_cells = tf.contrib.rnn.MultiRNNCell(decoding_cells, state_is_tuple=True)

        global_step = tf.train.get_or_create_global_step()
        # Outputs: (batch_size, timesteps, num_nodes, output_dim)
        with tf.variable_scope('DCRNN_SEQ'):
            inputs = tf.unstack(tf.reshape(self._inputs, (batch_size, seq_len, num_nodes * input_dim)), axis=1)
            labels = tf.unstack(tf.reshape(self._labels, (batch_size, horizon, num_nodes * output_dim)), axis=1)
            labels.insert(0, GO_SYMBOL)
            loop_function = None
            if is_training:
                if use_curriculum_learning:
                    def loop_function(prev, i):
                        c = tf.random_uniform((), minval=0, maxval=1.)
                        threshold = self._compute_sampling_threshold(global_step, cl_decay_steps)
                        result = tf.cond(tf.less(c, threshold), lambda: labels[i], lambda: prev)
                        return result
            else:
                # Return the output of the model.
                def loop_function(prev, _):
                    return prev

            _, enc_state = tf.contrib.rnn.static_rnn(encoding_cells, inputs, dtype=tf.float32)
            outputs, final_state = legacy_seq2seq.rnn_decoder(labels, enc_state, decoding_cells,
                                                              loop_function=loop_function)

        # Project the output to output_dim.
        outputs = tf.stack(outputs[:-1], axis=1)
        self._outputs = tf.reshape(outputs, (batch_size, horizon, num_nodes, output_dim), name='outputs')

        preds = self._outputs[..., 0]
        labels = self._labels[..., 0]

        null_val = config.get('null_val', 0.)
        self._mae = masked_mae_loss(self._scaler, null_val)(preds=preds, labels=labels)

        if loss_func == 'MSE':
            self._loss = masked_mse_loss(self._scaler, null_val)(preds=self._outputs, labels=self._labels)
        elif loss_func == 'MAE':
            self._loss = masked_mae_loss(self._scaler, null_val)(preds=self._outputs, labels=self._labels)
        elif loss_func == 'RMSE':
            self._loss = masked_rmse_loss(self._scaler, null_val)(preds=self._outputs, labels=self._labels)
        else:
            self._loss = masked_mse_loss(self._scaler, null_val)(preds=self._outputs, labels=self._labels)
        if is_training:
            optimizer = tf.train.AdamOptimizer(self._lr)
            tvars = tf.trainable_variables()
            grads = tf.gradients(self._loss, tvars)
            grads, _ = tf.clip_by_global_norm(grads, max_grad_norm)
            self._train_op = optimizer.apply_gradients(zip(grads, tvars), global_step=global_step, name='train_op')

        self._merged = tf.summary.merge_all()

    @staticmethod
    def _compute_sampling_threshold(global_step, k):
        """
        Computes the sampling probability for scheduled sampling using inverse sigmoid.
        :param global_step:
        :param k:
        :return:
        """
        return tf.cast(k / (k + tf.exp(global_step / k)), tf.float32)
