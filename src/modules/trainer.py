import numpy as np
import os.path as osp
import os
import time
import keras.optimizers as optimizers
from keras.callbacks import LambdaCallback, ReduceLROnPlateau, ModelCheckpoint, TensorBoard, EarlyStopping
from modules.images_viewer import ImagesViewer
from modules.utils import preprocess_batch
from modules.tensors_stats import get_stats
from modules.detector import FCNDetector
import config


class Trainer:
    def __init__(self):
        self.fcn_model = None
        self.model = None
        self.dataset = None
        self.writer = None
        self.images_viewer = None
        self.merged = None
        self.last_checked_time = 0
        self.global_step = 0
        self.detector = FCNDetector()
    
    def generator(self, is_train):
        while 1:
            images, masks = self.dataset.get_batch(is_train=is_train, use_augmentation=is_train)
            yield preprocess_batch(images), masks
    
    def on_batch_end(self, batch, _):
        self.global_step += 1
        current_time = time.time()
    
        if current_time - self.last_checked_time > config.show_outputs_update_time:
            init_images, init_masks = self.dataset.get_batch(is_train=1, use_augmentation=1)
            max_index = np.argmax([mask.sum() for mask in init_masks])
            images = init_images[max_index:max_index + 1]
            masks = init_masks[max_index:max_index + 1]
            preprocessed_images = preprocess_batch(images)

            predictions = self.model.predict(preprocessed_images)
    
            if config.show_outputs_progress:
                image = images[0].copy()

                pred_nms_heat_map = self.detector.heat_map_nms(predictions[0])
                pred_rects = self.detector.obtain_rects(pred_nms_heat_map, predictions[0])
                pred_reduced_rects = FCNDetector.rects_nms(pred_rects)
                for rect in pred_reduced_rects:
                    rect.draw(image, (255, 0, 0), 2)

                true_nms_heat_map = self.detector.heat_map_nms(masks[0])
                true_rects = self.detector.obtain_rects(true_nms_heat_map, masks[0])
                true_reduced_rects = FCNDetector.rects_nms(true_rects)
                for rect in true_reduced_rects:
                    rect.draw(image, (0, 255, 0), 1)

                prediction = predictions[0] * 255
                mask = masks[0] * 255
                self.images_viewer.set_images([image, mask, prediction])

            if config.show_stats:
                stats = get_stats(self.fcn_model, preprocess_batch(init_images))
                print('\n')
                for stat in stats:
                    print(stat.to_string())
                print('\n')
            self.last_checked_time = current_time

    def train(self, fcn_model, dataset):
        def on_batch_end(*args, **kwargs):
            return self.on_batch_end(args, kwargs)

        def generator(*args, **kwargs):
            return self.generator(*args, **kwargs)

        weights_dir = fcn_model.weights_dir
        self.fcn_model = fcn_model
        self.model = fcn_model.model
        self.dataset = dataset

        sgd = optimizers.SGD(lr=config.initial_learning_rate, decay=1e-6, momentum=0.9, nesterov=True)
        self.model.compile(loss='mean_squared_error', optimizer=sgd, loss_weights=[1])
    
        if config.show_outputs_progress:
            self.images_viewer = ImagesViewer()
            self.images_viewer.start()

        batch_callback = LambdaCallback(on_batch_end=on_batch_end)
        callbacks = []
        if config.show_outputs_progress:
            callbacks.append(batch_callback)

        reduce_lr = ReduceLROnPlateau(monitor='val_loss', factor=0.6,
                                      patience=4, min_lr=0.00001, verbose=1, min_delta=1e-6)
        early_stopping = EarlyStopping(monitor='val_loss', patience=18, verbose=1, min_delta=1e-6)
        callbacks.append(reduce_lr)
        callbacks.append(early_stopping)

        if not osp.exists(weights_dir):
            os.makedirs(weights_dir)

        save_path1 = osp.join(weights_dir, 'weights.{epoch:02d}-{val_loss:.8f}.hdf5')
        save_path2 = osp.join(weights_dir, 'best_weights.hdf5')
        if config.save_model:
            check_pointer1 = ModelCheckpoint(save_path1, save_best_only=True, verbose=1, period=10)
            check_pointer2 = ModelCheckpoint(save_path2, save_best_only=True, verbose=1)

            if config.save_checkpoints:
                callbacks.append(check_pointer1)

            callbacks.append(check_pointer2)

            logs_dir = osp.join(weights_dir, 'logs')
            if not osp.exists(logs_dir):
                os.makedirs(logs_dir)

            callbacks.append(TensorBoard(logs_dir))

        if osp.exists(save_path2) and config.load_weights:
            self.model.load_weights(save_path2)

        steps_per_epoch_multiplier = 32/config.batch_shape[0]*2
        self.model.fit_generator(generator(True),
                                 steps_per_epoch=int(len(self.dataset.train_indices)*steps_per_epoch_multiplier),
                                 epochs=config.epochs_count,
                                 validation_data=generator(False),
                                 validation_steps=int(len(self.dataset.test_indices)*steps_per_epoch_multiplier),
                                 callbacks=callbacks)

        if config.show_outputs_progress:
            self.images_viewer.alive = False
            self.images_viewer.join()
