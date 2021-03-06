# -*- coding: utf-8 -*-

from __future__ import absolute_import, print_function, unicode_literals

import argparse
import cPickle as pickle
import gc
import numpy as np
import os
import shutil
import sys

from scipy.sparse import csr_matrix
from sklearn.externals import joblib
from sklearn.linear_model import SGDClassifier
from sklearn.metrics import accuracy_score, precision_score, recall_score
from sklearn.preprocessing import normalize
from utils import LABELS_REPLACEMENT


MODELS = [
    ("SVM", SGDClassifier)
]


def run_classifier(model_name, model_class, features_type, dataset, labels, classes, indices):
    configs = {
        "verbose": 1,
        "n_jobs": 12
    }

    if model_name == 'LR':
        configs['loss'] = 'log'

    model = model_class(**configs)

    print('Fitting model', file=sys.stderr, flush=True)
    model.fit(dataset[indices['train_indices']], labels[indices['train_indices']])

    print('Classifying test set', file=sys.stderr, flush=True)
    y_true = labels[indices['test_indices']]
    y_pred = model.predict(dataset[indices['test_indices']])

    print('Saving classification results', file=sys.stderr, flush=True)
    save_dir = os.path.join(args.results_dir, model_name, features_type)
    if os.path.exists(save_dir):
        shutil.rmtree(save_dir)
    os.makedirs(save_dir)

    header = ','.join(classes).encode('utf-8')

    # Accuracy
    accuracy = accuracy_score(y_true, y_pred)
    np.savetxt(os.path.join(save_dir, 'test_accuracy_NEU.txt'), np.array([accuracy], dtype=np.float32),
               fmt='%.3f'.encode('utf-8'), delimiter=','.encode('utf-8'))

    # Precision
    precision = precision_score(y_true, y_pred, average=None, labels=np.arange(classes.shape[0]))
    np.savetxt(os.path.join(save_dir, 'test_precision_NEU.txt'), np.array([precision], dtype=np.float32),
               fmt='%.3f'.encode('utf-8'), delimiter=','.encode('utf-8'), header=header)

    # Recall
    recall = recall_score(y_true, y_pred, average=None, labels=np.arange(classes.shape[0]))
    np.savetxt(os.path.join(save_dir, 'test_recall_NEU.txt'), np.array([recall], dtype=np.float32),
               fmt='%.3f'.encode('utf-8'), delimiter=','.encode('utf-8'), header=header)

    print('Saving model', file=sys.stderr, flush=True)
    joblib.dump(model, os.path.join(save_dir, '{}_model.pkl'.format(model_name)))

    print('Finished handcrafted experiment for classifier {}'.format(model_name), file=sys.stderr, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=unicode)
    parser.add_argument("labels", type=unicode)
    parser.add_argument("indices", type=unicode)
    parser.add_argument("results_dir", type=unicode)
    parser.add_argument("--wvdataset", type=unicode, default=None)
    parser.add_argument("--wvlabels", type=unicode, default=None)
    parser.add_argument("--mapping_kind", type=unicode, default='NEU')
    parser.add_argument("--mappings", type=unicode, default=None)
    parser.add_argument("--experiment_kind", type=unicode, default='legal')

    args = parser.parse_args()

    print('Loading dataset from file {}'.format(args.dataset), file=sys.stderr, flush=True)

    # First run on handcrafted dataset
    dataset = np.load(args.dataset)
    dataset = csr_matrix((dataset['data'], dataset['indices'], dataset['indptr']), shape=dataset['shape'])

    print('Loading labels from file {}'.format(args.labels), file=sys.stderr, flush=True)
    with open(args.labels, 'rb') as f:
        labels = pickle.load(f)

    replacement_function = LABELS_REPLACEMENT[args.experiment_kind][args.mapping_kind]
    mappings = np.load(args.mappings) if args.mappings is not None else None

    print('Replacing the labels', file=sys.stderr, flush=True)
    labels = list(replacement_function(labels, mappings))

    print('Loading indices for train, test and validation', file=sys.stderr, flush=True)
    indices = np.load(args.indices)

    print('Filtering dataset and labels according to indices', file=sys.stderr, flush=True)
    dataset = dataset[indices['filtered_indices']]
    labels = np.array(labels)[indices['filtered_indices']]
    classes, integer_labels = np.unique(labels, return_inverse=True)

    print('Normalizing dataset', file=sys.stderr, flush=True)
    dataset = normalize(dataset.astype(np.float32), norm='max', axis=0)

    for model_name, model_class in MODELS:
        print('Running handcrafted features dataset with {} classifier'.format(model_name), file=sys.stderr, flush=True)

        try:
            run_classifier(model_name, model_class, 'handcrafted', dataset, integer_labels, classes, indices)
        except Exception as e:
            print('The classifier {} throw an exception: {}'.format(model_name, e), file=sys.stderr, flush=True)
        finally:  # Release memory
            gc.collect()

        print('Finished handcrafted experiments with {} classifier'.format(model_name), file=sys.stderr, flush=True)

    if args.wvdataset is None or args.wvlabels is None:
        print('Finished all experiments', file=sys.stderr, flush=True)
        sys.exit(os.EX_OK)
    else:
        print('Finished all handcrafted experiments', file=sys.stderr, flush=True)

    print('Loading dataset from file {}. Filtering dataset according to indices'.format(args.wvdataset),
          file=sys.stderr, flush=True)

    dataset = np.load(args.wvdataset)['dataset'][indices['filtered_indices']]

    print('Loading word vectors labels from file {}'.format(args.wvlabels), file=sys.stderr, flush=True)
    with open(args.wvlabels, 'rb') as f:
        labels = pickle.load(f)

    print('Replacing the labels', file=sys.stderr, flush=True)
    labels = list(replacement_function(labels, mappings))

    print('Loading indices for train, test and validation', file=sys.stderr, flush=True)
    indices = np.load(args.indices)

    print('Filtering labels according to indices', file=sys.stderr, flush=True)
    labels = np.array(labels)[indices['filtered_indices']]
    classes, integer_labels = np.unique(labels, return_inverse=True)

    for model_name, model_class in MODELS:
        print('Running word vectors dataset with {} classifier'.format(model_name), file=sys.stderr, flush=True)

        try:
            run_classifier(model_name, model_class, 'wordvectors', dataset, labels, classes, indices)
        except Exception as e:
            print('The classifier {} throw an exception with message {}'.format(model_name, e.message), file=sys.stderr, flush=True)

        print('Finished word vectors experiments with {} classifier'.format(model_name), file=sys.stderr, flush=True)

    print('Finished all experiments')
