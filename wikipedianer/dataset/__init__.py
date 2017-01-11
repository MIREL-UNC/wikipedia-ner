# -*- coding: utf-8 -*-

from __future__ import absolute_import, print_function, unicode_literals

import gensim
import itertools
import numpy as np
try:
    import cPickle as pickle
except ImportError:
    import pickle
import sys

from collections import namedtuple
from scipy.sparse import csr_matrix
from sklearn.preprocessing import normalize
from tqdm import tqdm
from wikipedianer.pipeline.util import CL_ITERATIONS


DataTuple = namedtuple('DataTuple', ['data', 'labels'])


class Dataset(object):
    def __init__(self, dataset_path, labels_path, indices_path, dtype=np.float32):
        self.classes = ()

        self.train_dataset = np.array([])
        self.train_labels = np.array([])

        self.test_dataset = np.array([])
        self.test_labels = np.array([])

        self.validation_dataset = np.array([])
        self.validation_labels = np.array([])

        self.dtype = dtype

        self.__load_data__(dataset_path, labels_path, indices_path)

        self._datasets = {
            'train': DataTuple(self.train_dataset, self.train_labels),
            'test': DataTuple(self.test_dataset, self.test_labels),
            'validation': DataTuple(self.validation_dataset, self.validation_labels)
        }

        self._epochs_completed = 0
        self._index_in_epoch = 0

    def __load_data__(self, dataset_path, labels_path, indices_path):
        raise NotImplementedError

    def _one_hot_encoding(self, slice_, cl_iteration):
        raise NotImplementedError

    def num_examples(self, dataset_name='train'):
        return self._datasets[dataset_name].labels.shape[0]

    @property
    def input_size(self):
        raise NotImplementedError
    
    def output_size(self, cl_iteration):
        return self.classes[cl_iteration].shape[0]

    def reset_index_in_epoch(self):
        self._index_in_epoch = 0

    def next_batch(self, batch_size, cl_iteration):
        raise NotImplementedError

    def dataset_labels(self, dataset_name, cl_iteration):
        return self._datasets[dataset_name].labels[:, cl_iteration]

    def traverse_dataset(self, dataset_name, batch_size):
        raise NotImplementedError


class HandcraftedFeaturesDataset(Dataset):
    def __load_data__(self, dataset_path, labels_path, indices_path):
        print('Loading dataset from file %s' % dataset_path, file=sys.stderr, flush=True)
        dataset = np.load(dataset_path)
        dataset = csr_matrix((dataset['data'], dataset['indices'], dataset['indptr']), shape=dataset['shape'])

        print('Loading labels from file %s' % labels_path, file=sys.stderr, flush=True)
        with open(labels_path, 'rb') as f:
            labels = np.array(pickle.load(f))[:, ::-1]  # Reverse the columns order to follow the right flow of CL

        print('Loading the indices for train, test and validation from %s' % indices_path, file=sys.stderr, flush=True)
        indices = np.load(indices_path)

        labels = labels[indices['filtered_indices']]

        classes = []
        print('Getting classes for each iteration', file=sys.stderr, flush=True)
        for idx, iteration in enumerate(CL_ITERATIONS):
            replaced_labels = np.array([label[idx] for label in labels])
            classes.append(np.unique(replaced_labels, return_inverse=True))

        self.classes = tuple([cls[0] for cls in classes])

        print('Normalizing dataset', file=sys.stderr, flush=True)
        dataset = dataset[indices['filtered_indices']]
        dataset = normalize(dataset.astype(self.dtype), norm='max', axis=0)

        self.train_dataset = dataset[indices['train_indices']]
        self.test_dataset = dataset[indices['test_indices']]
        self.validation_dataset = dataset[indices['validation_indices']]

        integer_labels = np.stack([cls[1] for cls in classes]).T

        self.train_labels = integer_labels[indices['train_indices']]
        self.test_labels = integer_labels[indices['test_indices']]
        self.validation_labels = integer_labels[indices['validation_indices']]

    def _one_hot_encoding(self, slice_, cl_iteration):
        return np.eye(self.output_size(cl_iteration), dtype=self.dtype)[slice_.astype(np.int32)]

    def next_batch(self, batch_size, cl_iteration):
        start = self._index_in_epoch
        self._index_in_epoch += batch_size

        if self._index_in_epoch > self.num_examples():
            # Finished epoch
            self._epochs_completed += 1
            # Shuffle the data
            perm = np.arange(self.num_examples())
            np.random.shuffle(perm)
            self.train_dataset = self.train_dataset[perm]
            self.train_labels = self.train_labels[perm]
            # Start next epoch
            start = 0
            self._index_in_epoch = batch_size
            assert batch_size <= self.num_examples()

        end = self._index_in_epoch

        return (self.train_dataset[start:end].toarray(),
                self._one_hot_encoding(self.train_labels[start:end][:, cl_iteration], cl_iteration))

    @property
    def input_size(self):
        return self.train_dataset.shape[1]

    def traverse_dataset(self, dataset_name, batch_size):
        dataset, _ = self._datasets[dataset_name]

        for step in tqdm(np.arange(dataset.shape[0], step=batch_size)):
            yield step, dataset[step:min(step+batch_size, dataset.shape[0])].toarray()


class WordVectorsDataset(Dataset):
    def __init__(self, dataset_path, labels_path, indices_path, word_vectors_path, dtype=np.float32, debug=False):
        super(WordVectorsDataset, self).__init__(dataset_path, labels_path, indices_path, dtype)
        self.debug = debug
        self.__load_word_vectors__(word_vectors_path)

    def __load_data__(self, dataset_path, labels_path, indices_path):
        print('Loading dataset from file %s' % dataset_path, file=sys.stderr, flush=True)
        with open(dataset_path, 'rb') as f:
            dataset = pickle.load(f)

        print('Loading labels from file %s' % labels_path, file=sys.stderr, flush=True)
        with open(labels_path, 'rb') as f:
            labels = np.array(pickle.load(f))[:, ::-1]  # Reverse the columns order to follow the right flow of CL

        print('Loading the indices for train, test and validation from %s' % indices_path, file=sys.stderr, flush=True)
        indices = np.load(indices_path)

        labels = labels[indices['filtered_indices']]

        classes = []
        print('Getting classes for each iteration', file=sys.stderr, flush=True)
        for idx, iteration in enumerate(CL_ITERATIONS):
            replaced_labels = np.array([label[idx] for label in labels])
            classes.append(np.unique(replaced_labels, return_inverse=True))

        self.classes = tuple([cls[0] for cls in classes])

        print('Filtering and splitting dataset', file=sys.stderr, flush=True)
        dataset = [dataset[i] for i in indices['filtered_indices']]

        self.train_dataset = [dataset[i] for i in indices['train_indices']]
        self.test_dataset = [dataset[i] for i in indices['test_indices']]
        self.validation_dataset = [dataset[i] for i in indices['validation_indices']]

        integer_labels = np.stack([cls[1] for cls in classes]).T

        self.train_labels = integer_labels[indices['train_indices']]
        self.test_labels = integer_labels[indices['test_indices']]
        self.validation_labels = integer_labels[indices['validation_indices']]

    def __load_word_vectors__(self, word_vectors_path):
        print('Loading word vectors', file=sys.stderr)
        if self.debug:
            self._word_vector_model = gensim.models.Word2Vec()
        else:
            self._word_vector_model = gensim.models.Word2Vec.load_word2vec_format(word_vectors_path, binary=True)
        self._input_size = self._word_vector_model.vector_size * len(self.train_dataset[0])
        self._vector_size = self._word_vector_model.vector_size

    def _one_hot_encoding(self, slice_, cl_iteration):
        return np.eye(self.output_size(cl_iteration), dtype=self.dtype)[slice_.astype(np.int32)]

    def _word_window_to_vector(self, word_window):
        vector = []

        for word in word_window:
            if word[0] in self._word_vector_model:
                vector.append(self._word_vector_model[word[0]])
            elif word[1] in self._word_vector_model:
                vector.append(self._word_vector_model[word[1]])
            else:
                vector.append(np.zeros(self.vector_size, dtype=self.dtype))

        return np.concatenate(vector)

    def _data_slice_to_vectors(self, data_slice):
        return np.vstack([self._word_window_to_vector(ww) for ww in data_slice])

    def next_batch(self, batch_size, cl_iteration):
        start = self._index_in_epoch
        self._index_in_epoch += batch_size

        if self._index_in_epoch > self.num_examples():
            # Finished epoch
            self._epochs_completed += 1
            # Shuffle the data
            perm = np.arange(self.num_examples())
            np.random.shuffle(perm)
            self.train_dataset = [self.train_dataset[i] for i in perm]
            self.train_labels = self.train_labels[perm]
            # Start next epoch
            start = 0
            self._index_in_epoch = batch_size
            assert batch_size <= self.num_examples()

        end = self._index_in_epoch

        return (self._data_slice_to_vectors(self.train_dataset[start:end]),
                self._one_hot_encoding(self.train_labels[start:end][:, cl_iteration], cl_iteration))

    @property
    def input_size(self):
        return self._input_size

    @property
    def vector_size(self):
        return self._vector_size

    def traverse_dataset(self, dataset_name, batch_size):
        dataset, _ = self._datasets[dataset_name]

        for step in tqdm(np.arange(len(dataset), step=batch_size)):
            yield step, self._data_slice_to_vectors(dataset[step:min(step+batch_size, len(dataset))])
