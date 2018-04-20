from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import numpy as np
import pandas as pd
import pickle
import tensorflow as tf

flags = tf.app.flags
FLAGS = flags.FLAGS

flags.DEFINE_string('sensor_ids_filename', 'data/sensor_graph/graph_sensor_ids.txt',
                    'File containing sensor ids separated by comma.')
flags.DEFINE_string('distances_filename', 'data/sensor_graph/distances_la_2012.csv',
                    'CSV file containing sensor distances with three columns: [from, to, distance].')
flags.DEFINE_float('normalized_k', 0.1, 'Entries that become lower than normalized_k after normalization '
                                        'are set to zero for sparsity.')
flags.DEFINE_string('output_pkl_filename', 'data/sensor_graph/adj_mat.pkl', 'Path of the output file.')


def get_adjacency_matrix(distance_df, sensor_ids, normalized_k=0.1):
    """

    :param distance_df: data frame with three columns: [from, to, distance].
    :param sensor_ids: list of sensor ids.
    :param normalized_k: entries that become lower than normalized_k after normalization are set to zero for sparsity.
    :return:
    """
    num_sensors = len(sensor_ids)
    dist_mx = np.zeros((num_sensors, num_sensors), dtype=np.float32)
    dist_mx[:] = np.inf
    # Builds sensor id to index map.
    sensor_id_to_ind = {}
    for i, sensor_id in enumerate(sensor_ids):
        sensor_id_to_ind[sensor_id] = i

    # Fills cells in the matrix with distances.
    for row in distance_df.values:
        if row[0] not in sensor_id_to_ind or row[1] not in sensor_id_to_ind:
            continue
        dist_mx[sensor_id_to_ind[row[0]], sensor_id_to_ind[row[1]]] = row[2]

    # Calculates the standard deviation as theta.
    distances = dist_mx[~np.isinf(dist_mx)].flatten()
    std = distances.std()
    adj_mx = np.exp(-np.square(dist_mx / std))
    # Make the adjacent matrix symmetric by taking the max.
    # adj_mx = np.maximum.reduce([adj_mx, adj_mx.T])

    # Sets entries that lower than a threshold, i.e., k, to zero for sparsity.
    adj_mx[adj_mx < normalized_k] = 0
    return sensor_ids, sensor_id_to_ind, adj_mx


if __name__ == '__main__':
    with open(FLAGS.sensor_ids_filename) as f:
        sensor_ids = f.read().strip().split(',')
    distance_df = pd.read_csv(FLAGS.distances_filename, dtype={'from': 'str', 'to': 'str'})
    _, sensor_id_to_ind, adj_mx = get_adjacency_matrix(distance_df, sensor_ids)
    # Save to pickle file.
    with open(FLAGS.output_pkl_filename, 'wb') as f:
        pickle.dump([sensor_ids, sensor_id_to_ind, adj_mx], f)
