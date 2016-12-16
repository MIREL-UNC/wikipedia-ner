# -*- coding: utf-8 -*-

from __future__ import absolute_import, print_function, unicode_literals

import tensorflow as tf
import numpy as np
import os
import pandas as pd
import sys
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score
from base import BaseClassifier


class MultilayerPerceptron(BaseClassifier):
    def __init__(self, dataset, pre_trained_weights_save_path='', results_save_path='',
                 experiment_name='', cl_iteration=0, layers=[],
                 learning_rate=0.01, training_epochs=10000, batch_size=2100, loss_report=250, pre_weights=None,
                 pre_biases=None, save_model=False, dropout_ratios=None, batch_normalization=False):
        """
        :type dataset: wikipedianer.dataset.Dataset
        :type pre_trained_weights_save_path: str
        :type results_save_path: str
        :type experiment_name: str
        :type layers: list[int]
        :type learning_rate: float
        :type training_epochs: int
        :type batch_size: int
        :type loss_report: int
        :type pre_weights: np.ndarray
        :type pre_biases: np.ndarray
        :type save_model: bool
        :type dropout_ratios: list(float)
        :type batch_normalization: bool
        """
        self.check_batch_size(batch_size, dataset)

        self.dataset = dataset
        self.cl_iteration = cl_iteration

        self.X = tf.placeholder(tf.float32, shape=(None, self.dataset.input_size), name='X')
        self.y = tf.placeholder(tf.float32, shape=(None, self.dataset.output_size(self.cl_iteration)), name='y')
        self.training_epochs = training_epochs
        self.batch_size = batch_size

        self.layers = [self.X]
        self.weights = []
        self.biases = []
        self.keep_probs = []
        self.keep_probs_ratios = []

        dropout_ratios = [] if dropout_ratios is None else dropout_ratios

        # Create the layers
        for layer_idx, (size_prev, size_current) in enumerate(zip([self.dataset.input_size] + layers, layers)):
            print('Creating hidden layer %02d: %d -> %d' % (layer_idx, size_prev, size_current), file=sys.stderr, flush=True)

            layer_name = 'hidden_layer_%02d' % layer_idx

            try:
                self.keep_probs_ratios.append(1.0 - dropout_ratios.pop(0))
            except IndexError:
                self.keep_probs_ratios.append(1.0)

            with tf.name_scope(layer_name):
                if pre_weights and layer_name in pre_weights:
                    weights = tf.Variable(pre_weights[layer_name], name='weights')
                else:
                    weights = tf.Variable(
                        tf.truncated_normal([size_prev, size_current],
                                            stddev=1.0 / np.sqrt(size_prev)),
                        name='weights'
                    )

                if pre_biases and layer_name in pre_biases:
                    biases = tf.Variable(pre_biases[layer_name], name='biases')
                else:
                    biases = tf.Variable(tf.zeros([size_current]), name='biases')

                keep_prob = tf.placeholder(tf.float32, name='keep_prob')
                layer = tf.nn.relu(tf.matmul(self.layers[-1], weights) + biases)

                if batch_normalization:
                    mean, var = tf.nn.moments(layer, axes=[0])
                    layer = tf.nn.batch_normalization(layer, mean, var, None, None, 1e-10)

                regularized_layer = tf.nn.dropout(layer, keep_prob)
                self.layers.append(regularized_layer)
                self.weights.append(weights)
                self.biases.append(biases)
                self.keep_probs.append(keep_prob)

        # The last layer is for the classifier
        with tf.name_scope('softmax_layer'):
            if len(layers) == 0:
                last_layer = self.dataset.input_size
            else:
                last_layer = layers[-1]
            print('Creating softmax layer: %d -> %d' % (last_layer, self.dataset.output_size(self.cl_iteration)),
                  file=sys.stderr, flush=True)
            if pre_weights and 'softmax_layer' in pre_weights:
                weights = tf.Variable(pre_weights['softmax_layer'], name='weights')
            else:
                weights = tf.Variable(
                    tf.truncated_normal([last_layer, self.dataset.output_size(self.cl_iteration)],
                                        stddev=1.0 / np.sqrt(last_layer)),
                    name='weights'
                )

            if pre_biases and 'softmax_layer' in pre_biases:
                biases = tf.Variable(pre_biases['softmax_layer'], name='biases')
            else:
                biases = tf.Variable(tf.zeros([self.dataset.output_size(self.cl_iteration)]), name='biases')

            self.y_logits = tf.matmul(self.layers[-1], weights) + biases

        self.loss = tf.reduce_mean(
            tf.nn.softmax_cross_entropy_with_logits(self.y_logits, self.y),
            name='cross_entropy_mean_loss'
        )
        self.y_pred = tf.argmax(tf.nn.softmax(self.y_logits), 1, name='y_predictions')

        self.learning_rate = tf.Variable(learning_rate, trainable=False)
        global_step = tf.Variable(0, name='global_step', trainable=False)

        # Add a scalar summary for the snapshot loss.
        tf.scalar_summary(self.loss.op.name, self.loss)
        self.train_step = tf.train.AdamOptimizer(self.learning_rate).minimize(self.loss, global_step=global_step)

        self.init = tf.initialize_all_variables()

        # results and saves
        self.pre_trained_weights_save_path = pre_trained_weights_save_path
        self.experiment_name = experiment_name
        self.results_save_path = results_save_path
        self.train_loss_record = []
        self.validation_accuracy_record = []
        self.test_results = pd.DataFrame(columns=['accuracy', 'class', 'precision', 'recall', 'fscore'])
        self.test_predictions_results = pd.DataFrame(columns=['true', 'prediction'])
        self.loss_report = loss_report
        self.saver = tf.train.Saver() if save_model else None

    def check_batch_size(self, batch_size, dataset):
        error_message = ('The batch size cannot be larger than the number of '
                         'examples training, test or validation datasets')
        assert batch_size <= dataset.num_examples('train'), error_message
        if dataset.num_examples('test') > 1:
            assert batch_size <= dataset.num_examples('test'), error_message
        if dataset.num_examples('validation') > 1:
            assert batch_size <= dataset.num_examples('validation'), \
                error_message

    def _evaluate(self, sess, dataset_name, return_extras=False):
        y_pred = np.zeros(self.dataset.num_examples(dataset_name), dtype=np.int32)

        print('Running evaluation for dataset %s' % dataset_name, file=sys.stderr, flush=True)
        for step, dataset_chunk in self.dataset.traverse_dataset(dataset_name, self.batch_size):
            feed_dict = {
                self.X: dataset_chunk
            }

            for keep_prob in self.keep_probs:
                feed_dict[keep_prob] = 1.0

            y_pred[step:min(step+self.batch_size, self.dataset.num_examples(dataset_name))] =\
                sess.run(self.y_pred, feed_dict=feed_dict)

        y_true = self.dataset.dataset_labels(dataset_name, self.cl_iteration)
        accuracy = accuracy_score(y_true, y_pred.astype(y_true.dtype))

        if not return_extras:
            return accuracy
        else:
            precision = precision_score(y_true, y_pred, labels=np.arange(self.dataset.output_size(self.cl_iteration)),
                                        average=None)
            recall = recall_score(y_true, y_pred, labels=np.arange(self.dataset.output_size(self.cl_iteration)),
                                  average=None)
            fscore = f1_score(y_true, y_pred, labels=np.arange(self.dataset.output_size(self.cl_iteration)),
                              average=None)

            return accuracy, precision, recall, fscore, y_true, y_pred

    def _save_results(self, save_layers):
        # Train loss
        np.savetxt(os.path.join(self.results_save_path, 'train_loss_record_%s.txt' % self.experiment_name),
                   np.array(self.train_loss_record, dtype=np.float32), fmt='%.3f', delimiter=',')

        # Validation accuracy
        np.savetxt(os.path.join(self.results_save_path, 'validation_accuracy_record_%s.txt' % self.experiment_name),
                   np.array(self.validation_accuracy_record, dtype=np.float32), fmt='%.3f', delimiter=',')

        # Test
        self.test_results.to_csv(os.path.join(self.results_save_path, 'test_results_%s.csv' % self.experiment_name),
                                 index=False)
        self.test_predictions_results.to_csv(
            os.path.join(self.results_save_path, 'test_predictions_%s.csv' % self.experiment_name), index=False)

        if save_layers:
            print('Saving weights and biases', file=sys.stderr, flush=True)
            file_name_weights = os.path.join(self.pre_trained_weights_save_path,
                                             "%s_weights.npz" % self.experiment_name)
            file_name_biases = os.path.join(self.pre_trained_weights_save_path,
                                            "%s_biases.npz" % self.experiment_name)

            weights_dict = {}
            biases_dict = {}

            for layer_idx, (weights, biases) in enumerate(zip(self.weights, self.biases)):
                layer_name = 'hidden_layer_%02d' % layer_idx
                weights_dict[layer_name] = weights.eval()
                biases_dict[layer_name] = biases.eval()

            np.savez_compressed(file_name_weights, **weights_dict)
            np.savez_compressed(file_name_biases, **biases_dict)

    def train(self, save_layers=True):
        with tf.Session() as sess:
            sess.run(tf.initialize_all_variables())

            for epoch in np.arange(self.training_epochs):
                batch_dataset, batch_labels = self.dataset.next_batch(self.batch_size, self.cl_iteration)

                feed_dict = {
                    self.X: batch_dataset,
                    self.y: batch_labels
                }

                for idx, keep_prob in enumerate(self.keep_probs):
                    feed_dict[keep_prob] = self.keep_probs_ratios[idx]

                _, loss = sess.run([self.train_step, self.loss], feed_dict=feed_dict)

                # We record the loss every `loss_report` iterations
                if epoch > 0 and epoch % self.loss_report == 0:
                    print('Epoch %d: loss = %.3f' % (epoch, loss), file=sys.stderr, flush=True)
                    self.train_loss_record.append(loss)

                # We check the validation accuracy every `loss_report`*2 iterations
                if epoch > 0 and epoch % (self.loss_report * 4) == 0:
                    accuracy = self._evaluate(sess, 'validation')
                    print('Validation accuracy: %.3f' % accuracy, file=sys.stderr, flush=True)
                    self.validation_accuracy_record.append(accuracy)

                    if round(accuracy, 2) == 1 or (self.cl_iteration < 2 and accuracy >= 0.99):
                        print('Validation accuracy maxed: %.2f' % round(accuracy, 1), file=sys.stderr, flush=True)
                        break

                    if len(self.validation_accuracy_record) >= 2:
                        delta_acc = max(self.validation_accuracy_record) - accuracy

                        if delta_acc > 0.01:
                            print('Validation accuracy converging: delta_acc %.3f' % delta_acc, file=sys.stderr,
                                  flush=True)
                            break

                        # If there hasn't been any significant change in the last 5 iterations, stop
                        if len(self.validation_accuracy_record) >= 5 and self.validation_accuracy_record[-1] >= 0.95:
                            change = (max(self.validation_accuracy_record[-5:]) -
                                      min(self.validation_accuracy_record[-5:]))
                            if change < 0.01:
                                print('Validation accuracy unchanged for a large period', file=sys.stderr, flush=True)
                                break
                        elif len(self.validation_accuracy_record) >= 10 and self.validation_accuracy_record[-1] >= 0.85:
                            change = (max(self.validation_accuracy_record[-10:]) -
                                      min(self.validation_accuracy_record[-10:]))
                            if change < 0.01:
                                print('Validation accuracy unchanged for a large period', file=sys.stderr, flush=True)
                                break

            print('Finished training', file=sys.stderr, flush=True)

            accuracy, precision, recall, fscore, y_true, y_pred = self._evaluate(sess, 'test', True)
            print('Testing accuracy: %.3f' % accuracy, file=sys.stderr, flush=True)

            self.test_results = self.test_results.append({'accuracy': accuracy}, ignore_index=True)
            for cls_idx, cls in enumerate(self.dataset.classes[self.cl_iteration]):
                self.test_results = self.test_results.append({
                    'class': cls,
                    'precision': precision[cls_idx],
                    'recall': recall[cls_idx],
                    'fscore': fscore[cls_idx]
                }, ignore_index=True)

            self.test_predictions_results = pd.DataFrame(np.vstack([y_true, y_pred]).T,
                                                         columns=self.test_predictions_results.columns)

            print('Saving results', file=sys.stderr, flush=True)
            self._save_results(save_layers)

            self.save_model(sess)

    def save_model(self, sess):
        if self.saver is not None:
            print('Saving model', file=sys.stderr, flush=True)
            save_path = self.saver.save(sess, os.path.join(
                self.results_save_path, '%s.model' % self.experiment_name))
            print('Model saved in file %s' % save_path, file=sys.stderr,
                  flush=True)

