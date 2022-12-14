from random import shuffle
from keras.utils import Sequence
from pathlib import Path
import os
import pandas as pd
import cv2
import preprocess
import numpy as np
from keras.layers import (
    Conv2D,
    Activation,
    BatchNormalization,
    UpSampling2D,
    Dense,
    MaxPooling2D,
    LeakyReLU,
    GlobalAveragePooling2D,
    Flatten,
    Conv2DTranspose,
    Add,
    concatenate,
    Input
)
from keras.metrics import MeanIoU, BinaryIoU
from keras.models import Model
from keras.optimizers import Adam
from matplotlib import pyplot as plt
from keras.callbacks import (
    ModelCheckpoint,
    EarlyStopping,
    ReduceLROnPlateau,
    TensorBoard,
    LearningRateScheduler
)
from tensorflow_addons.losses import GIoULoss, giou_loss
import pickle

import logging
logging.basicConfig(filename='cryo-em-select.log',
                    encoding='utf-8', level=logging.DEBUG)


class CryoBatchGenerator(Sequence):
    """
    This is a generator for batches of training data. It reads in a list of paths to the training images. We preprocess the images
    and create the labels from the .csv files.
    """

    def __init__(self, X, batch_size, image_size=(224, 224, 1), shuffle=False, save_labels=False, label_type='gauss'):
        """
        Initialize a batch generator.

        :param X: List of image paths
        :param batch_size: Number of images in each batch
        :param image_size: Dimension of each image
        :param shuffle: Whether to shuffle the images
        :param save_labels: Whether to save the labels for the images
        """

        self.X = X
        self.batch_size = batch_size
        self.image_size = image_size
        self.shuffle = shuffle
        self.save_labels = save_labels
        self.label_type = label_type
        self.n = len(X)

        logging.debug("Created CryoBatchGenerator")

    def on_epoch_end(self):
        """
        This method shuffle the training data at the end of a epoch.

        :return: None
        """
        logging.debug("epoch ended shuffle training data")
        if self.shuffle:
            np.random.shuffle(self.X)

    def __getitem__(self, index):
        """
        This method generate one batch of data.

        :param index: index of training data
        :return: Return a batch.
                    First output is the training data
                    Second output is the label for the images
        """
        logging.debug(
            "Generating one batch of data for batch index: {index}".format(index=index))
        batch = self.X[index * self.batch_size:(index + 1) * self.batch_size]
        X, Y = self.__get_data(batch)

        return X, Y

    def __get_data(self, batch):
        """
        This method extracts the labels for all images in the given batch.

        :param batch: Batch of image paths
        :return: Return a batch.
                    First output is the raw images
                    Second output is the label for the images
        """
        logging.debug("Creating batch")
        # __get_input return tuples, thus we convert them to list of tuples and further map to a list
        X_batch, Y_batch = zip(*[self.__get_input(x) for x in batch])

        # Collapse the first dimensions.
        X_batch = np.asarray(X_batch)
        #X_batch = np.concatenate(X_batch, axis=0)

        Y_batch = np.asarray(Y_batch)
        #Y_batch = np.concatenate(Y_batch, axis=0)
        
        return X_batch, Y_batch

    def __get_input(self, path):
        """
        This method labels a image given by its path.

        :param path: The path to the image file
        :return: Return images
                    First output is the original image scaled
                    Second output is the labels for the images
        """
        logging.debug("Add labels for image: {path}".format(path=path))
        # image_height = 622
        # image_width = 900

        image_height = 640
        image_width = 880

        label_path = Path(
            str(os.getcwd()) + '/data/label_annotation/' + path.stem + '-points' + '.csv')
        label_df = pd.DataFrame()
        if label_path.exists():
            logging.debug(" - Labels found")
            label_df = pd.read_csv(label_path, header=None)
            label_df = label_df.round(0).astype(int)
        else:
            logging.debug(" - Labels not found")

        # Coordinate system turns in a weird way.
        points = [(rows[1], rows[0]) for _, rows in label_df.iterrows()]

        # Load greyscale to remove the 3 channels
        img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)

        gauss_img = preprocess.GaussianHighlight(
            img[:, :], points, 32, self.label_type)

        img = img[21:-21, 40:-40]
        gauss_img = gauss_img[21:-21, 40:-40]


        image = img / 255.
        image = (image - image.mean()) / image.std()

        gauss_image = gauss_img / 255.

        cropped_images = []
        cropped_label_images = []
        # 682, 960
        for i in range(self.image_size[0], image_height, self.image_size[0]):
            for j in range(self.image_size[1], image_width, self.image_size[1]):
                image = img[i-self.image_size[0]:i, j-self.image_size[1]:j]
                image = image.astype(float)
                image /= 255.
                # Zero center image
                image = (image - image.mean()) / image.std()
                # image = (2 * image) - 1

                gauss_image = gauss_img[i-self.image_size[0]: i, j-self.image_size[1]: j]
                gauss_image = gauss_image.astype(float)
                gauss_image /= 255.

                cropped_images.append(image)
                cropped_label_images.append(gauss_image)

        if self.save_labels:
            filename = Path(str(os.getcwd()) +
                            '/data/label_data/' + path.stem + '-points.jpg')
            if not filename.exists():
                cv2.imwrite(str(filename), gauss_img, [
                            cv2.IMWRITE_JPEG_QUALITY, 100])

        #return cropped_images, cropped_label_images
        return image, gauss_image

    def __len__(self):
        """
        Total number of batches.

        :return: The length of the
        """
        return self.n // self.batch_size


class CryoEmNet:
    """
    This is our cryo-em segmentation class
    """

    def __init__(self, batch_size, image_size=(224, 224, 1), model=None, label_type='gauss'):
        """
        :param batch_size: Batch size for training / prediction
        :param input_size: Input image size
        """
        logging.debug("Creating CryoEmNet")
        self.batch_size = batch_size
        self.image_size = image_size
        self.label_type = label_type

        if model is None:
            # self.model = self.build_unet()

            # Good base model
            # self.model = self.build_basic_model()

            # Prob good model (Need more training)
            # self.model = self.small_unet()

            # self.model = self.build_large_unet()
            self.build_large_residual_unet()
        else:
            self.model = model

        logging.debug("Created CryoEmNet")

    def __convolution_layer(self, x, filters, kernel_size=3, padding='same', kernel_initializer='he_normal'):
        """
        General convolution layer block that applies a convolution, batch normalization and further a activation function

        :param x: Input tensor
        :param filters: Integer, the dimensionality of the output space
        :param kernel_size: An integer or tuple/list of 2 integers, specifying the height and width of the 2D convolution window
        :param padding: Padding scheme
        :param kernel_initializer: Regularizer function

        :return: Returns a tensor from the convolution layers
        """
        x = Conv2D(filters, kernel_size, padding=padding,
                   kernel_initializer=kernel_initializer)(x)
        x = BatchNormalization()(x)
        x = LeakyReLU(alpha=0.1)(x)
        return x

    def __residual_module_type1(self, x, filters=64):
        """
        Creates a residual module of type 1

        :param x: Input tensor
        :param filters: Integer, the dimensionality of the output space 

        :return: Return output from the residual module type 1
        """
        #conv1 = Conv2D(filters, (3,3), padding='same', activation='relu', kernel_initializer='he_normal')(x)
        conv1 = self.__convolution_layer(
            x, filters=filters, padding='same', kernel_initializer='he_normal')

        #conv2 = Conv2D(filters, (3,3), padding='same', activation='linear', kernel_initializer='he_normal')(conv1)
        conv2 = self.__convolution_layer(
            conv1, filters=filters, padding='same', kernel_initializer='he_normal')
        # Add filters, assumes conv2 and x have the same shape
        y = Add()([conv2, x])

        # Activation function
        y = LeakyReLU(alpha=0.1)(y)
        return y

    def __residual_module_type2(self, x, filters=64):
        """
        Creates a residual module of type 2

        :param x: Input tensor
        :param filters: Integer, the dimensionality of the output space 

        :return: Return output from the residual module type 2
        """
        # conv1 (reduces height/width by a factor of two and doubles the number of channels)
        conv1 = Conv2D(2*filters, (3, 3), strides=2, padding='same',
                       activation='relu', kernel_initializer='he_normal')(x)
        # conv2 (same shape as conv1)
        conv2 = Conv2D(2*filters, (3, 3), padding='same',
                       activation='linear', kernel_initializer='he_normal')(conv1)
        # reshape x (same shape as conv2)
        x_reshape = Conv2D(2*filters, (1, 1), strides=2,
                           activation='linear')(x)
        # add filters, assumes conv2 and x have the same shape
        y = Add()([conv2, x_reshape])

        # Activation function
        y = LeakyReLU(alpha=0.1)(y)
        return y

    def build_basic_model(self):
        """
        Creates a basic model that intentionally don't have skip connections

        Overwrites classs model with Basic segmentation model 
        """
        logging.debug("Building basic model")

        logging.debug("Setting inputs")
        inputs = Input(shape=self.image_size)

        # Encoder
        logging.debug("Setting up encoder")
        convolution_1 = self.__convolution_layer(inputs, filters=8)
        pooling_1 = MaxPooling2D(pool_size=(2, 2))(convolution_1)
        convolution_2 = self.__convolution_layer(pooling_1, filters=16)
        pooling_2 = MaxPooling2D(pool_size=(2, 2))(convolution_2)
        convolution_3 = self.__convolution_layer(pooling_2, filters=32)
        pooling_3 = MaxPooling2D(pool_size=(2, 2))(convolution_3)
        convolution_4 = self.__convolution_layer(pooling_3, filters=64)

        # Decoder
        logging.debug("Setting up decoder")
        convolution_5 = self.__convolution_layer(convolution_4, filters=32)
        upsampling_1 = UpSampling2D(size=(2, 2))(convolution_5)
        convolution_6 = self.__convolution_layer(upsampling_1, filters=16)
        upsampling_2 = UpSampling2D(size=(2, 2))(convolution_6)
        convolution_7 = self.__convolution_layer(upsampling_2, filters=8)
        upsampling_3 = UpSampling2D(size=(2, 2))(convolution_7)

        logging.debug("Setting outputs")
        outputs = Conv2D(1, 1, activation='sigmoid')(upsampling_3)

        # Specify model
        logging.debug("Init model with inputs and outputs")
        basic_model = Model(inputs=inputs, outputs=outputs)
        basic_model.summary()

        self.model = basic_model

        logging.debug("Created basic model")

    def small_unet(self):
        """
        Creates a small unet model that indeed does have skip connections

        Overwrites classs model with Small unet model
        """
        logging.debug("Building small unet")
        logging.debug("Setting inputs")
        inputs = Input(shape=self.image_size)

        # Encoder
        logging.debug("Setting up encoder")
        convolution_1 = self.__convolution_layer(inputs, filters=8)
        pooling_1 = MaxPooling2D(pool_size=(2, 2))(convolution_1)
        convolution_2 = self.__convolution_layer(pooling_1, filters=16)
        pooling_2 = MaxPooling2D(pool_size=(2, 2))(convolution_2)
        convolution_3 = self.__convolution_layer(pooling_2, filters=32)
        pooling_3 = MaxPooling2D(pool_size=(2, 2))(convolution_3)
        convolution_4 = self.__convolution_layer(pooling_3, filters=64)

        # Decoder
        logging.debug("Setting up decoder")
        upsampling_1 = UpSampling2D(size=(2, 2))(convolution_4)
        merge_1 = concatenate([convolution_3, upsampling_1], axis=3)
        upsampling_2 = UpSampling2D(size=(2, 2))(merge_1)
        merge_2 = concatenate([convolution_2, upsampling_2], axis=3)
        upsampling_3 = UpSampling2D(size=(2, 2))(merge_2)
        merge_3 = concatenate([convolution_1, upsampling_3], axis=3)

        logging.debug("Setting outputs")
        outputs = Conv2D(1, 1, activation='sigmoid')(merge_3)

        # Specify model
        logging.debug("Init model with inputs and outputs")
        small_unet = Model(inputs=inputs, outputs=outputs)
        small_unet.summary()

        self.model = small_unet

        logging.debug("Created small unet")

    def build_large_residual_unet(self):
        """
        Creates a large unet segmentation model that applies both the residual type 1 and 2 modules. Further the model should also include skip connections

        Overwrites classs model with Large unet residual segmentation model
        """
        logging.debug("Building large residual unet")
        logging.debug("Setting inputs")
        inputs = Input(shape=self.image_size)

        # Encoder
        logging.debug("Setting up encoder")
        # Layer 1
        convolution_1 = self.__residual_module_type1(inputs, filters=8)
        pooling_1 = self.__residual_module_type2(convolution_1, filters=8)

        # Layer 2
        convolution_2 = self.__residual_module_type1(pooling_1, filters=16)
        pooling_2 = self.__residual_module_type2(convolution_2, filters=16)

        # Layer 3
        convolution_3 = self.__residual_module_type1(pooling_2, filters=32)

        # Layer 4
        convolution_4 = self.__residual_module_type1(convolution_3, filters=32)

        # Layer 5
        convolution_5 = self.__residual_module_type1(convolution_4, filters=32)
        pooling_3 = self.__residual_module_type2(convolution_5, filters=32)

        # Layer 6
        convolution_6 = self.__residual_module_type1(pooling_3, filters=64)

        # Layer 7
        convolution_7 = self.__residual_module_type1(convolution_6, filters=64)

        # Layer 8
        convolution_8 = self.__residual_module_type1(convolution_7, filters=64)
        pooling_4 = self.__residual_module_type2(convolution_8, filters=64)

        # Layer 9
        convolution_9 = self.__residual_module_type1(pooling_4, filters=128)

        # # Layer 10
        # convolution_10 = self.__residual_module_type1(convolution_9, filters=128)

        # # Layer 11
        # convolution_11 = self.__residual_module_type1(convolution_10, filters=128)

        # # Layer 12
        # convolution_12 = self.__residual_module_type1(convolution_11, filters=128)

        # # Layer 13
        # convolution_13 = self.__residual_module_type1(convolution_12, filters=128)

        # # Layer 14
        # convolution_14 = self.__residual_module_type1(convolution_13, filters=128)

        # # Layer 15
        # convolution_15 = self.__residual_module_type1(convolution_14, filters=128)

        # Decoder
        logging.debug("Setting up decoder")
        # Layer 16
        convolution_16 = self.__residual_module_type1(
            convolution_9, filters=128)
        upsampling_1 = Conv2DTranspose(filters=64, kernel_size=1, strides=2,
                                       padding='same', kernel_initializer='he_normal')(convolution_16)
        merge_1 = concatenate([convolution_7, upsampling_1], axis=3)

        # Layer 17
        convolution_filter_resize_1 = self.__convolution_layer(
            merge_1, filters=64)
        convolution_17 = self.__residual_module_type1(
            convolution_filter_resize_1, filters=64)

        # Layer 18
        convolution_18 = self.__residual_module_type1(
            convolution_17, filters=64)

        # Layer 19
        convolution_19 = self.__residual_module_type1(
            convolution_18, filters=64)
        upsampling_2 = Conv2DTranspose(
            32, (1, 1), strides=(2, 2))(convolution_19)
        merge_2 = concatenate([convolution_4, upsampling_2], axis=3)

        # Layer 20
        convolution_filter_resize_2 = self.__convolution_layer(
            merge_2, filters=32)
        convolution_20 = self.__residual_module_type1(
            convolution_filter_resize_2, filters=32)
        upsampling_3 = Conv2DTranspose(
            16, (1, 1), strides=(2, 2))(convolution_20)
        merge_3 = concatenate([convolution_2, upsampling_3], axis=3)

        # Layer 21
        convolution_filter_resize_3 = self.__convolution_layer(
            merge_3, filters=16)
        convolution_21 = self.__residual_module_type1(
            convolution_filter_resize_3, filters=16)
        upsampling_4 = Conv2DTranspose(
            16, (1, 1), strides=(2, 2))(convolution_21)
        merge_4 = concatenate([convolution_1, upsampling_4], axis=3)

        logging.debug("Setting outputs")
        outputs = Conv2D(1, 1, activation='sigmoid')(merge_4)

        # Specify model
        logging.debug("Init model with inputs and outputs")
        large_residual_unet = Model(inputs=inputs, outputs=outputs)
        large_residual_unet.summary()

        self.model = large_residual_unet

        logging.debug("Created large residual unet")

    def build_large_unet(self):
        """
        Creates a large unet segmentation model. We have extended the amount of layers, but still have only a few skip connections. 

        Overwrites classs model with Large unet segmentation model
        """
        logging.debug("Building large unet")
        logging.debug("Setting inputs")
        inputs = Input(shape=self.image_size)

        # Encoder
        logging.debug("Setting up encoder")
        # Layer 1
        convolution_1 = self.__convolution_layer(inputs, filters=8)
        pooling_1 = MaxPooling2D(pool_size=(2, 2))(convolution_1)

        # Layer 2
        convolution_2 = self.__convolution_layer(pooling_1, filters=16)
        pooling_2 = MaxPooling2D(pool_size=(2, 2))(convolution_2)

        # Layer 3
        convolution_3 = self.__convolution_layer(pooling_2, filters=32)

        # Layer 4
        convolution_4 = self.__convolution_layer(convolution_3, filters=16)

        # Layer 5
        convolution_5 = self.__convolution_layer(convolution_4, filters=32)
        pooling_3 = MaxPooling2D(pool_size=(2, 2))(convolution_5)

        # Layer 6
        convolution_6 = self.__convolution_layer(pooling_3, filters=64)

        # Layer 7
        convolution_7 = self.__convolution_layer(convolution_6, filters=32)

        # Layer 8
        convolution_8 = self.__convolution_layer(convolution_7, filters=64)
        pooling_4 = MaxPooling2D(pool_size=(2, 2))(convolution_8)

        # Layer 9
        convolution_9 = self.__convolution_layer(pooling_4, filters=128)

        # Layer 10
        convolution_10 = self.__convolution_layer(convolution_9, filters=64)

        # Layer 11
        convolution_11 = self.__convolution_layer(convolution_10, filters=128)

        # Layer 12
        convolution_12 = self.__convolution_layer(convolution_11, filters=64)

        # Layer 13
        convolution_13 = self.__convolution_layer(convolution_12, filters=128)

        # Layer 14
        convolution_14 = self.__convolution_layer(convolution_13, filters=64)

        # Layer 15
        convolution_15 = self.__convolution_layer(convolution_14, filters=128)

        # Decoder
        logging.debug("Setting up decoder")
        # Layer 16
        convolution_16 = self.__convolution_layer(convolution_15, filters=64)
        upsampling_1 = UpSampling2D(size=(2, 2))(convolution_16)
        merge_1 = concatenate([convolution_7, upsampling_1], axis=3)

        # Layer 17
        convolution_17 = self.__convolution_layer(merge_1, filters=32)

        # Layer 18
        convolution_18 = self.__convolution_layer(convolution_17, filters=64)

        # Layer 19
        convolution_19 = self.__convolution_layer(convolution_18, filters=32)
        upsampling_2 = UpSampling2D(size=(2, 2))(convolution_19)
        merge_2 = concatenate([convolution_4, upsampling_2], axis=3)

        # Layer 20
        convolution_20 = self.__convolution_layer(merge_2, filters=16)
        upsampling_3 = UpSampling2D(size=(2, 2))(convolution_20)
        merge_3 = concatenate([convolution_2, upsampling_3], axis=3)

        # Layer 21
        convolution_21 = self.__convolution_layer(merge_3, filters=16)
        upsampling_4 = UpSampling2D(size=(2, 2))(convolution_21)
        merge_4 = concatenate([convolution_1, upsampling_4], axis=3)

        logging.debug("Setting output")
        outputs = Conv2D(1, 1, activation='sigmoid')(merge_4)

        # Specify model
        logging.debug("Init model with inputs and outputs")
        large_unet = Model(inputs=inputs, outputs=outputs)
        large_unet.summary()

        self.model = large_unet

        logging.debug("Created large unet")

    def build_unet(self):
        """
        Creates a unet model that has some intermediate convolutions, such that it becomes a bit larger

        Overwrites classs model with Unet segmentation model
        """
        logging.debug("Building unet")
        logging.debug("Setting inputs")
        inputs = Input(shape=self.image_size)

        # Encoder
        logging.debug("Setting up encoder")
        convolution_1 = self.__convolution_layer(inputs, filters=8)
        pooling_1 = MaxPooling2D(pool_size=(2, 2))(convolution_1)
        convolution_2 = self.__convolution_layer(pooling_1, filters=16)
        pooling_2 = MaxPooling2D(pool_size=(2, 2))(convolution_2)
        convolution_3 = self.__convolution_layer(pooling_2, filters=32)
        pooling_3 = MaxPooling2D(pool_size=(2, 2))(convolution_3)
        convolution_4 = self.__convolution_layer(pooling_3, filters=64)

        # Decoder
        logging.debug("Setting up decoder")
        convolution_5 = self.__convolution_layer(convolution_4, filters=32)
        upsampling_1 = UpSampling2D(size=(2, 2))(convolution_5)

        convolution_5_1 = self.__convolution_layer(upsampling_1, filters=32)
        convolution_5_2 = self.__convolution_layer(convolution_5_1, filters=32)

        merge_1 = concatenate([convolution_3, convolution_5_2], axis=3)

        convolution_6 = self.__convolution_layer(merge_1, filters=16)
        upsampling_2 = UpSampling2D(size=(2, 2))(convolution_6)

        convolution_6_1 = self.__convolution_layer(upsampling_2, filters=16)
        convolution_6_2 = self.__convolution_layer(convolution_6_1, filters=16)

        merge_2 = concatenate([convolution_2, convolution_6_2], axis=3)

        convolution_7 = self.__convolution_layer(merge_2, filters=8)
        upsampling_3 = UpSampling2D(size=(2, 2))(convolution_7)

        convolution_7_1 = self.__convolution_layer(upsampling_3, filters=16)
        convolution_7_2 = self.__convolution_layer(convolution_7_1, filters=16)

        merge_3 = concatenate([convolution_1, convolution_7_2], axis=3)

        logging.debug("Setting outputs")
        outputs = Conv2D(1, 1, activation='sigmoid')(merge_3)

        # Specify model
        logging.debug("Init model with inputs and outputs")
        custom_unet = Model(inputs=inputs, outputs=outputs)
        custom_unet.summary()

        self.model = custom_unet

        logging.debug("Created unet")

    def train(
        self,
        filepath: Path = Path(str(os.getcwd())) / 'model' / 'checkpoint',
        nb_epoch_early=10,
        warmrestarts=True,
        learning_rate=10 ** -2,
        epochs=10,
        save_log=True,
        save_model=True,
        use_all_data=False
    ):
        """
        Function that trains the CryoEmNet model. Method also handles all callback functionality, specify the model optimizer and compilers the model.

        :param filepath: Path to where we save the model
        :param nb_epoch_early: Number of epochs before we consider early stopping. This is also used when considering if we should reduce the learning rate on a plateau.
        :param warmrestarts:  This warm-starting approach enables us to start training from a better initial point on the loss surface and often learn better models
        :param learning_rate: The model learning rate
        :param epochs: Number of epochs
        :param save_log: Whether to save the log file
        :param save_model: Whether to save the model
        """
        logging.debug("Starting train")
        logging.debug(" - filepath: {filepath}".format(filepath=filepath))
        logging.debug(
            " - nb_epoch_early: {nb_epoch_early}".format(nb_epoch_early=nb_epoch_early))
        logging.debug(
            " - warmrestarts: {warmrestarts}".format(warmrestarts=warmrestarts))
        logging.debug(
            " - learning_rate: {learning_rate}".format(learning_rate=learning_rate))
        logging.debug(" - epochs: {epochs}".format(epochs=epochs))
        logging.debug(" - save_log: {save_log}".format(save_log=save_log))
        logging.debug(
            " - save_model: {save_model}".format(save_model=save_model))

        data_path = [x for x in Path(
            str(os.getcwd()) + '/data/raw_data/').iterdir()]
        if not use_all_data:
            data_path = data_path[:200]

        logging.debug("data_path = {data_path}".format(data_path=data_path))

        # Generator with the training data
        logging.debug("Creating training data generator")
        train_generator = CryoBatchGenerator(
            X=data_path[:int(len(data_path) * 0.8)],
            batch_size=self.batch_size,
            image_size=self.image_size,
            shuffle=True,
            save_labels=True,
            label_type=self.label_type
        )

        # Generator with the validation data
        logging.debug("Creating validation data generator")
        validation_generator = CryoBatchGenerator(
            X=data_path[int(len(data_path) * 0.8):],
            batch_size=self.batch_size,
            image_size=self.image_size,
            shuffle=True,
            save_labels=True,
            label_type=self.label_type
        )

        # Define callbacks
        logging.debug("Defining callbacks")
        
        all_callbacks = []
        # Tried to remove the checkpoint callback
        checkpoint = ModelCheckpoint(
            filepath=filepath,
            monitor="accuracy",
            verbose=1,
            save_best_only=True,
            save_weights_only=True,
            mode="max",
            save_freq='epoch',
        )
        all_callbacks.append(checkpoint)

        early_stop = EarlyStopping(
            monitor="accuracy",
            min_delta=0.0005,
            patience=nb_epoch_early,
            mode="max",
            verbose=1,
        )
        all_callbacks.append(early_stop)

        reduceLROnPlateau = ReduceLROnPlateau(
            monitor="accuracy",
            factor=0.1,
            patience=int(nb_epoch_early * 0.6),
            verbose=1,
        )
        all_callbacks.append(reduceLROnPlateau)
        if save_log:
            try:
                os.makedirs(os.path.expanduser("logs/"))
            except:
                pass

            tb_counter = (
                len(
                    [
                        log
                        for log in os.listdir(os.path.expanduser("logs/"))
                        if "cinderella" in log
                    ]
                )
                + 1
            )
            tensorboard = TensorBoard(
                log_dir=os.path.expanduser(
                    "logs/") + "cinderella" + "_" + str(tb_counter),
                histogram_freq=1,
                write_graph=True,
                write_images=True,
            )
            all_callbacks.append(tensorboard)
        if not warmrestarts:
            reduce_lr_on_plateau = ReduceLROnPlateau(
                monitor="accuracy",
                factor=0.1,
                patience=int(nb_epoch_early * 0.6),
                verbose=1,
            )
            all_callbacks.append(reduce_lr_on_plateau)
        else:
            def scheduler(epochs, learning_rate):
                if epochs < 10:
                    return learning_rate
                else:
                    import tensorflow as tf
                    return learning_rate * tf.math.exp(-0.1)
            schedule = LearningRateScheduler(
                schedule=scheduler,
                verbose=True
            )

            all_callbacks.append(schedule)

        # Model optimizer
        logging.debug("Creating model optimizer")
        optimizer = Adam(
            learning_rate=learning_rate,
            beta_1=0.9,
            beta_2=0.999,
            epsilon=1e-07,
            amsgrad=False,
            name="Adam",
        )

        def dice_loss(y_true, y_pred, smooth=1):
            import keras.backend as K
            '''
            dice coefficient =2*sum(|y_true*y_pred|)/(sum(y_true^2)+sum(y_pred^2))
            
            Args:
            ->ground truth label
            ->predicted label
            -smooth:default is 1

            https://github.com/tensorflow/addons/pull/2558/commits/fa02a90d838b6e521c8f5b1ae2fd6c0a4bd2b794
            '''
            y_true = float(y_true)
            y_pred = float(y_pred)
            intersection = K.sum(K.abs(y_true*y_pred), axis=-1)
            return 1-(2. * intersection + smooth) / (K.sum(K.square(y_true), -1) + K.sum(K.square(y_pred), -1) - intersection + smooth)

        logging.debug("Compiling model")
        self.model.compile(
            optimizer=optimizer,
            # loss='mse'
            # loss=giou_loss,
            # loss=GIoULoss(),
            loss=dice_loss,
            # metrics=['accuracy'],
            metrics=[BinaryIoU(name='IoU'), 'accuracy']
        )

        logging.debug("Fitting model")
        history = self.model.fit(
            train_generator,
            validation_data=validation_generator,
            epochs=epochs,
            callbacks=all_callbacks
        )
        
        logging.debug("Saving the model and history")
        if save_model:  
            self.model.save(str(filepath))
        if save_log:
            with open(filepath / 'train_history', 'wb') as f:
                pickle.dump(history.history, f)


        # Functionality to delete the label data from the label_data folder
        logging.debug("Outputting label data")
        folder = Path(os.getcwd()) / 'data' / 'label_data'
        for filename in folder.iterdir():
            file_path = folder / Path(filename)
            try:
                if file_path.exists():
                    os.remove(str(file_path))
            except Exception as e:
                print('Failed to delete %s. Reason: %s' % (file_path, e))

    def predict(self, img):
        """
        Method to enable the model to predict on an given image

        :param img: Image to predict

        :return: The result of the model prediction on the given image
        """
        result = self.model.predict(img)

        return result

    def show_predictions(self, image_name: Path, label_type):
        """
        Method that shows the predictions on a given image. 

        :param image_name: The Path of the input image
        """
        import matplotlib.gridspec as gridspec
        # image_height = 622
        # image_width = 900

        image_height = 640
        image_width = 880

        # path = str(os.getcwd()) + '/data/raw_data/' + image_name.name
        path = Path(os.getcwd()) / 'data_exploration' / 'raw_data' / image_name.name
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)

        # path = str(os.getcwd()) + '/data/label_data/' + \
        #     image_name.stem + '-points' + '.jpg'
        test = str(image_name.stem) + '-points' + '-' + label_type + '.jpg'
        path = Path(os.getcwd()) / 'data_exploration' / 'label_data' / test
        label_image = cv2.imread(str(path))

        resized_images = []
        resized_label_images = []
        # 682, 960
        for i in range(self.image_size[0], image_height, self.image_size[0]):
            for j in range(self.image_size[1], image_width, self.image_size[1]):
                image_resize = image[i-self.image_size[0]
                    :i, j-self.image_size[1]:j]
                image_resize = image_resize.astype(float)
                image_resize /= 255.
                # Zero center image
                image_resize = (
                    image_resize - image_resize.mean()) / image_resize.std()

                label_image_resize = label_image[i -
                                                 self.image_size[0]:i, j-self.image_size[1]:j]
                label_image_resize = label_image_resize.astype(float)
                label_image_resize /= 255.
                
                resized_images.append(image_resize)
                resized_label_images.append(label_image_resize)
                
        resized_images = np.asarray(resized_images)
        resized_label_images = np.asarray(resized_label_images)
        
        out = self.predict(resized_images[:, :, :])

        fig = plt.figure(figsize=(20, 8))

        outer = gridspec.GridSpec(
            image_width // self.image_size[1], image_height // self.image_size[0], wspace=0.2, hspace=0.5)

        for i in range(len(resized_images)):
            inner = gridspec.GridSpecFromSubplotSpec(1, 3,
                                                     subplot_spec=outer[i], wspace=0.2, hspace=0.5)

            ax = plt.Subplot(fig, inner[0])
            ax.imshow(resized_images[i, :, :], cmap='gray')
            ax.set_title('Input image', fontsize=10)
            ax.get_xaxis().set_visible(False)
            ax.get_yaxis().set_visible(False)
            fig.add_subplot(ax)

            ax = plt.Subplot(fig, inner[1])
            ax.imshow(out[i, :, :], vmin=0, vmax=1, cmap='gray')
            ax.set_title('Predicted', fontsize=10)
            ax.get_xaxis().set_visible(False)
            ax.get_yaxis().set_visible(False)
            fig.add_subplot(ax)

            ax = plt.Subplot(fig, inner[2])
            ax.imshow(resized_label_images[i, :, :], vmin=0, vmax=1)
            ax.set_title('Ground truth', fontsize=10)
            ax.get_xaxis().set_visible(False)
            ax.get_yaxis().set_visible(False)
            fig.add_subplot(ax)

        plt.show()