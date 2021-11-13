import joblib
import numpy as np
import torch

from sklearn.cluster import KMeans

from early_classifier.base import BaseClassifier


class KMeansClassifier(BaseClassifier):


    def __init__(self, device, n_labels, k):
        super().__init__(device, n_labels)
        self.k = k
        self.model = KMeans(n_clusters=self.k)
        self.label_share = np.zeros([self.k, n_labels], dtype=int)
        self.confidence_share = {cluster: {label: [] for label in range(n_labels)} for cluster in range(self.k)}
        self.max_labels = np.zeros(self.k, dtype=int)
        self.shares = np.zeros(self.k, dtype=float)
        self.cluster_sizes = np.zeros(self.k, dtype=int)
        self.valid_shares = None
        self.share_threshold = 0

    def fit(self, x, y, c):
        # clustering
        self.model.fit(x)

        # predict on the training set
        clusters = self.model.predict(x)

        # compute per-cluster shares
        for cluster, target, confidence in zip(clusters, y, c):
            if cluster != -1:
                if target < self.n_labels:
                    self.label_share[cluster][target] += 1
                    self.confidence_share[cluster][target].append(confidence)
        for cluster in self.confidence_share:
            for label in self.confidence_share[cluster]:
                if len(self.confidence_share[cluster][label]) > 0:
                    self.confidence_share[cluster][label] = np.mean(self.confidence_share[cluster][label])
                else:
                    self.confidence_share[cluster][label] = 0
        for cluster in range(self.k):
            self.max_labels[cluster] = np.argmax(self.label_share[cluster])
            if self.label_share[cluster][self.max_labels[cluster]] > 0:
                self.shares[cluster] = np.max(self.label_share[cluster]) / np.sum(self.label_share[cluster])
            self.cluster_sizes[cluster] = np.sum(self.label_share[cluster])

        # only keep clusters featuring more than one item
        self.valid_shares = [share for i, share in enumerate(self.shares) if self.cluster_sizes[i] > 1]
        self.share_threshold = np.quantile(self.valid_shares, 0.2)


    def predict(self, x):
        x = x.cpu()
        x = x.detach().numpy()
        x = x.reshape(x.shape[0], np.prod(x.shape[1:]))
        # TODO is it possible to work directly with tensors?
        y = np.zeros((x.shape[0], self.n_labels), dtype=np.float32)
        predicted_clusters = self.model.predict(x)
        for i, cluster in enumerate(predicted_clusters):
            if cluster != -1:
                # Naive confidence: fraction of label shares in the cluster
                y[i][self.max_labels[cluster]] = self.shares[cluster]
        return torch.Tensor(y).to(self.device)

    def get_prediction_confidences(self, y):
        return torch.max(y, 1)[0]

    def get_threshold(self):
        return self.share_threshold

    def init_results(self):
        d = dict()
        d['shares'] = self.valid_shares
        d['shares_mean'] = np.mean(self.valid_shares)
        return d

    def key_param(self):
        return self.k

    def save(self, filename):
        model_dict = dict({
            'model': self.model,
            'metadata': {
                'type': 'kmeans',
                'k': self.k,
                'max_labels': self.max_labels,
                'shares': self.shares,
                'valid_shares': self.valid_shares,
                'share_threshold': self.share_threshold
            }
        })
        joblib.dump(model_dict, open(filename, 'wb'))

    def load(self, filename):
        model_dict = joblib.load(filename)
        if model_dict['metadata']['type'] != 'kmeans':
            raise TypeError("Expected model type 'kmeans'.")
        self.model = model_dict['model']
        self.k = model_dict['metadata']['k']
        self.max_labels = model_dict['metadata']['max_labels']
        self.shares = model_dict['metadata']['shares']
        self.valid_shares = model_dict['metadata']['valid_shares']
        self.share_threshold = model_dict['metadata']['share_threshold']
