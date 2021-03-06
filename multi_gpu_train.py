#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 17/5/12
# @Author  : irmo
# This version is imitating cifar10_multi_gpu_train.py
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from datetime import datetime
import os.path
import re
import time

from six.moves import xrange
import numpy as np
import tensorflow as tf

from tensorflow.contrib.slim.python.slim.nets import vgg
from tensorflow.contrib.slim.python.slim.nets import resnet_v1, resnet_v2

slim = tf.contrib.slim
FLAGS = tf.app.flags.FLAGS

dataset = 'casia'
net = 'resnet_v1_50'
restore = True
restore_step = 133000

tf.app.flags.DEFINE_string('train_dir', os.path.join('train_data', dataset + '_' + net),
                           """Directory where to write event logs and checkpoint.""")
tf.app.flags.DEFINE_string('tfrecord_filename', os.path.join('tfrecord', dataset + '.tfrecord'),
                           """the name of the tfrecord""")
tf.app.flags.DEFINE_integer('max_steps', 1000000,
                            """Number of batches to run.""")
tf.app.flags.DEFINE_integer('num_gpus', 3,
                            """How many GPUs to use.""")
tf.app.flags.DEFINE_boolean('log_device_placement', False,
                            """Whether to log device placement.""")
tf.app.flags.DEFINE_integer('batch_size', 32, """Batch size""")
tf.app.flags.DEFINE_integer('num_classes', 10575, """Classes""")

TOWER_NAME = 'tower'
MOVING_AVERAGE_DECAY = 0.9999
NUM_IMAGES_PER_EPOCH = 445326 
NUM_EPOCHS_PER_DECAY = 20 

LEARNING_RATE_DECAY_FACTOR = 0.1
INITIAL_LEARNING_RATE = 0.01


def read_and_decode():
    """
    http://warmspringwinds.github.io/tensorflow/tf-slim/2016/12/21/tfrecords-guide/
    """
    filename = [FLAGS.tfrecord_filename]
    filename_queue = tf.train.string_input_producer(filename)

    reader = tf.TFRecordReader()
    _, serialized_example = reader.read(filename_queue)
    features = tf.parse_single_example(
        serialized_example,
        features={
            'image_raw': tf.FixedLenFeature([], tf.string),
            'label': tf.FixedLenFeature([], tf.int64)
        })
    image = tf.decode_raw(features['image_raw'], tf.uint8)
    label = tf.cast(features['label'], tf.int32)
    image = tf.reshape(image, [224, 224, 3])
    image = tf.cast(image, tf.float32)
    min_after_dequeue = 10000
    images, labels = tf.train.shuffle_batch([image, label],
                                            batch_size=FLAGS.batch_size,
                                            capacity=min_after_dequeue + 12 * FLAGS.batch_size,
                                            num_threads=8,
                                            min_after_dequeue=min_after_dequeue)
    return images, labels


def tower_loss(scope):
    images, labels = read_and_decode()
    if net == 'vgg_16':
        with slim.arg_scope(vgg.vgg_arg_scope()):
            logits, end_points = vgg.vgg_16(images, num_classes=FLAGS.num_classes)
    elif net == 'vgg_19':
        with slim.arg_scope(vgg.vgg_arg_scope()):
            logits, end_points = vgg.vgg_19(images, num_classes=FLAGS.num_classes)
    elif net == 'resnet_v1_101':
        with slim.arg_scope(resnet_v1.resnet_arg_scope()):
            logits, end_points = resnet_v1.resnet_v1_101(images, num_classes=FLAGS.num_classes)
        logits = tf.reshape(logits, [FLAGS.batch_size, FLAGS.num_classes])
    elif net == 'resnet_v1_50':
        with slim.arg_scope(resnet_v1.resnet_arg_scope()):
            logits, end_points = resnet_v1.resnet_v1_50(images, num_classes=FLAGS.num_classes)
        logits = tf.reshape(logits, [FLAGS.batch_size, FLAGS.num_classes])
    elif net == 'resnet_v2_50':
        with slim.arg_scope(resnet_v2.resnet_arg_scope()):
            logits, end_points = resnet_v2.resnet_v2_50(images, num_classes=FLAGS.num_classes)
        logits = tf.reshape(logits, [FLAGS.batch_size, FLAGS.num_classes])
    else:
        raise Exception('No network matched with net %s.' % net)
    assert logits.shape == (FLAGS.batch_size, FLAGS.num_classes)
    _ = cal_loss(logits, labels)
    losses = tf.get_collection('losses', scope)
    total_loss = tf.add_n(losses, name='total_loss')
    for l in losses + [total_loss]:
        loss_name = re.sub('%s_[0-9]*/' % TOWER_NAME, '', l.op.name)
        tf.summary.scalar(loss_name, l)
    return total_loss


def cal_loss(logits, labels):
    labels = tf.cast(labels, tf.int64)
    cross_entropy = tf.nn.sparse_softmax_cross_entropy_with_logits(
        labels=labels, logits=logits, name='cross_entropy_per_example'
    )
    cross_entropy_mean = tf.reduce_mean(cross_entropy, name='cross_entropy')
    tf.add_to_collection('losses', cross_entropy_mean)
    return tf.add_n(tf.get_collection('losses'), name='total_loss')


def average_gradients(tower_grads):
    average_grads = []
    for grad_and_vars in zip(*tower_grads):
        grads = []
        for g, _ in grad_and_vars:
            expanded_g = tf.expand_dims(g, 0)
            grads.append(expanded_g)
        grad = tf.concat(axis=0, values=grads)
        grad = tf.reduce_mean(grad, 0)

        v = grad_and_vars[0][1]
        grad_and_vars = (grad, v)
        average_grads.append(grad_and_vars)
    return average_grads


def train():
    with tf.Graph().as_default(), tf.device('/cpu:0'):
        global_step = tf.get_variable('global_step', [], initializer=tf.constant_initializer(0), trainable=False)
        num_batches_per_epoch = NUM_IMAGES_PER_EPOCH / FLAGS.batch_size
        decay_steps = int(num_batches_per_epoch * NUM_EPOCHS_PER_DECAY / FLAGS.num_gpus)
        print()
        print('    TRAIN  INFORMATION     ')
        print('Training dataset: %s ' % dataset)
        print('Training model  : %s' % net)
        print('Number of GPUs  : %d' % FLAGS.num_gpus)
        print('Batch size      : %d' % FLAGS.batch_size)
        print('Num batches per epoch: %d' % num_batches_per_epoch)
        print('Decay steps     : %d' % decay_steps)
        print('---------------------------')
        print()
        lr = tf.train.exponential_decay(INITIAL_LEARNING_RATE,
                                        global_step,
                                        decay_steps,
                                        LEARNING_RATE_DECAY_FACTOR,
                                        staircase=True)
        optimizer = tf.train.GradientDescentOptimizer(lr)
        tower_grads = []
        print('Building graph...')
        with tf.variable_scope(tf.get_variable_scope()):
            for i in xrange(FLAGS.num_gpus):
                with tf.device('/gpu:%d' % i):
                    with tf.name_scope('%s_%d' % (TOWER_NAME, i)) as scope:
                        loss = tower_loss(scope)
                        tf.get_variable_scope().reuse_variables()
                        if i == 0:
                            summaries = tf.get_collection(tf.GraphKeys.SUMMARIES, scope)
                        grads = optimizer.compute_gradients(loss)
                        tower_grads.append(grads)

        grads = average_gradients(tower_grads)
        summaries.append(tf.summary.scalar('learning_rate', lr))
        for grad, var in grads:
            if grad is not None:
                summaries.append(tf.summary.histogram(var.op.name + '/gradients', grad))

        apply_gradient_op = optimizer.apply_gradients(grads, global_step=global_step)
        for var in tf.trainable_variables():
            summaries.append(tf.summary.histogram(var.op.name, var))

        variables_averages = tf.train.ExponentialMovingAverage(MOVING_AVERAGE_DECAY, global_step)
        variables_averages_op = variables_averages.apply(tf.trainable_variables())

        train_op = tf.group(apply_gradient_op, variables_averages_op)

        saver = tf.train.Saver(tf.global_variables(), keep_checkpoint_every_n_hours=1)
        summary_op = tf.summary.merge(summaries)

        print('Creating session...')
        sess = tf.Session(config=tf.ConfigProto(
            allow_soft_placement=True,
            log_device_placement=FLAGS.log_device_placement))
        
        if not restore:
            init = tf.global_variables_initializer()
            print('Initializing session...')
            sess.run(init)

        print('Start queue runners...')
        tf.train.start_queue_runners(sess=sess)

        summary_writer = tf.summary.FileWriter(FLAGS.train_dir, sess.graph)
        
        step = 0
        if restore:
            print('Restoring model...')
            # saver.recover_last_checkpoints(FLAGS.train_dir)
            saver.restore(sess, os.path.join(FLAGS.train_dir, str(net) + '.ckpt-' + str(restore_step))) 
            print('Model restored.')
            step = int(sess.run([global_step])[0]) + 1
        
        print('Start training...')
        while True:
            start_time = time.time()
            _ = sess.run([train_op])
            duration = time.time() - start_time

            if step % 10 == 0:
                loss_value, learning_rate = sess.run([loss, optimizer._learning_rate])
                num_images_per_step = FLAGS.batch_size * FLAGS.num_gpus
                images_per_sec = num_images_per_step / duration
                sec_per_batch = duration / FLAGS.num_gpus
                format_str = '%s: step %d, loss = %.4f, learning rate = %.1e (%.1f images/sec; %.3f sec/batch)'
                print(format_str % (datetime.now(), step, loss_value, learning_rate, images_per_sec, sec_per_batch))

            if step % 100 == 0:
                summary_str = sess.run(summary_op)
                summary_writer.add_summary(summary_str, step)
                
            if step % 1000 == 0 or (step + 1) == FLAGS.max_steps:
                checkpoint_path = os.path.join(FLAGS.train_dir, str(net) + '.ckpt')
                saver.save(sess, checkpoint_path, global_step=step)
            step = step + 1

def main(argv=None):
    if not restore:
        if tf.gfile.Exists(FLAGS.train_dir):
            confirm = raw_input('Training data exists. Do you want to delete them? ')
            if confirm == 'y' or confirm == 'yes':
                tf.gfile.DeleteRecursively(FLAGS.train_dir)
                tf.gfile.MakeDirs(FLAGS.train_dir)
            else:
                print('Not delete the existed data. Start training.')
    train()


if __name__ == '__main__':
    tf.app.run()
