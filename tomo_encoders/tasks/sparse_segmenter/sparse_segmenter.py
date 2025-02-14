#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
class implementations for real-time 3D feature extraction


"""

import pandas as pd
import os
import glob
import numpy as np


# from skimage.feature import match_template
# from tomopy import normalize, minus_log, angles, recon, circ_mask
# from scipy.ndimage.filters import median_filter

from tomo_encoders.neural_nets.porosity_encoders import build_Unet_3D, custom_objects_dict
from tomo_encoders import Patches
from tomo_encoders import DataFile
import tensorflow as tf
from tensorflow.keras.models import load_model
from tensorflow.keras.layers import UpSampling3D
from multiprocessing import Pool, cpu_count
import functools

import h5py
import abc
import time


# complete once you write one or two feature extractors
# class AnyFeatureExtractor(metaclass = abc.ABCMeta):
    
#     @abc.abstractmethod
#     def do_something(self):
#         pass
#     @abc.abstractmethod
#     def do_something_else(self):
#         pass


class SparseSegmenter():
    def __init__(self,\
                 model_initialization = "define-new", \
                 model_size = (64,64,64), \
                 descriptor_tag = "misc", **model_params):
        '''
        
        Parameters
        ----------
        model_initialization : str
            either "define-new" or "load-model"
        
        model_size : tuple
            shape of the input patch required by model

        descriptor_tag : str
            some description used while saving the models

        model_params : dict
            If "define-new", this contains the hyperparameters that define the architecture of the neural network model. If "load-model", it contains the paths to the models.
            
        models : dict of tf.keras.Models.model 
            dict contains {"latent_embedder" : encoder_model, "CAE" : denoiser_model}. Here, both models are keras 3D models with input shape = model_size

        '''
        model_getters = {"load-model" : self._load_models, \
                         "define-new" : self._build_models, \
                         "load-weights" : self._load_weights}
        
        # any function chosen must assign self.models, self.model_tag and self.model_size
        
        if model_initialization == "define-new":
            model_getters[model_initialization](model_size = model_size, \
                                                descriptor_tag = descriptor_tag, \
                                                **model_params)
        elif model_initialization == "load-model":
            model_getters[model_initialization](**model_params)
        else:
            raise NotImplementedError("method is not implemented")
            
        return
            
    def save_models(self, model_path):
        
        for model_key in self.models.keys():
            self.models[model_key].save(os.path.join(model_path, "%s_%s.hdf5"%(model_key, self.model_tag)))
        return
    
    def _build_models(self, model_size = (64,64,64), descriptor_tag = "misc", **model_params):
        '''
        
        Parameters
        ----------
        model_keys : list  
            list of strings describing the model, e.g., ["segmenter"], etc.
        model_params : dict
            for passing any number of model hyperparameters necessary to define the model(s).
            
        '''
        if model_params is None:
            raise ValueError("Need model hyperparameters or instance of model. Neither were provided")
        else:
            self.models = {}

        # insert your model building code here. The models variable must be a dictionary of models with str descriptors as keys
        self.model_size = model_size
        self.model_tag = "Unet_%s"%(descriptor_tag)

        model_keys = ["segmenter"]
        for key in model_keys:
            self.models.update({key : None})
        self.models["segmenter"] = build_Unet_3D(self.model_size + (1,), **model_params)
        self.models["segmenter"].compile(optimizer=tf.keras.optimizers.Adam(),\
                      loss=tf.keras.losses.BinaryCrossentropy())
        return
    
    def _load_weights(self, **kwargs):
        raise NotImplementedError("Not implemented yet")
        
    def print_layers(self, modelkey):
        
        txt_out = []
        for ii in range(len(self.models[modelkey].layers)):
            lshape = str(self.models[modelkey].layers[ii].output_shape)
            lname = str(self.models[modelkey].layers[ii].name)
            txt_out.append(lshape + "    ::    "  + lname)
            
        print('\n'.join(txt_out))
        return
        
    def _load_models(self, model_names = None, model_path = 'some/path'):
        
        '''
        Parameters
        ----------
        model_names : dict
            example {"segmenter" : "Unet"}
        model_path : str  
            example "some/path"
        custom_objects_dict : dict  
            dictionary of custom objects (usually pickled with the keras models)
            
        '''
        self.models = {} # clears any existing models linked to this class!!!!
        for model_key, model_name in model_names.items():
            self.models.update({model_key : load_model(os.path.join(model_path, model_name + '.hdf5'), \
                                                      custom_objects = custom_objects_dict)})
        # insert assignment of model_size here
        self.model_size = self.models["segmenter"].input_shape[1:-1]
        self.model_tag = "_".join(model_names["segmenter"].split("_")[1:])
        return

    def _normalize_volume(self, vol):
        '''
        Normalizes volume to values into range [0,1]  

        '''
        eps = 1e-12
        max_val = np.max(vol)
        min_val = np.min(vol)
        vol = (vol - min_val) / (max_val - min_val + eps)
        return vol

    def _read_data_pairs(self, ds_X, ds_Y, s_crops):
        
        print("loading data...")
        X = ds_X.read_full().astype(np.float32)
        Y = ds_Y.read_full().astype(np.uint8)
        
        X = X[s_crops].copy()
        Y = Y[s_crops].copy()
        
        # normalize volume, check if shape is compatible.  
        X = self._normalize_volume(X).astype(np.float16)
        print("done")
        print("Shape X %s, shape Y %s"%(str(X.shape), str(Y.shape)))
        return X, Y
    
    def load_datasets(self, datasets):
    
        '''
        Parameters  
        ----------  
        
        '''
        n_vols = len(datasets)
        
        Xs = [0]*n_vols
        Ys = [0]*n_vols
        ii = 0
        for filename, dataset in datasets.items():
            
            ds_X = DataFile(dataset['fpath_X'], tiff = False, \
                            data_tag = dataset['data_tag_X'], VERBOSITY = 0)
            
            ds_Y = DataFile(dataset['fpath_Y'], tiff = False, \
                            data_tag = dataset['data_tag_Y'], VERBOSITY = 0)
            
            Xs[ii], Ys[ii] = self._read_data_pairs(ds_X, ds_Y, dataset['s_crops'])
            ii += 1
        del ii
        return Xs, Ys
    
    def train(self, Xs, Ys, batch_size, \
              sampling_method, n_epochs,\
              random_rotate = False, \
              add_noise = 0.1,\
              max_stride = 1, \
              cutoff = 0.0):
        
        '''
        Parameters  
        ----------  
        
        '''
        n_vols = len(Xs)
        # instantiate data generator for use in training.  
        dg = self.data_generator(Xs, Ys, batch_size, sampling_method, \
                                 max_stride = max_stride, \
                                 random_rotate = random_rotate, \
                                 add_noise = add_noise, \
                                 cutoff = cutoff)
        tot_steps = 1000
        val_split = 0.2
        steps_per_epoch = int((1-val_split)*tot_steps//batch_size)
        validation_steps = int(val_split*tot_steps//batch_size)

        t0 = time.time()
        self.models["segmenter"].fit(x = dg, epochs = n_epochs,\
                  steps_per_epoch=steps_per_epoch,\
                  validation_steps=validation_steps, verbose = 1)    
        t1 = time.time()
        training_time = (t1 - t0)
        print("training time = %.2f seconds"%training_time)        
        
        return

    
    def _get_xy_noblanks(self, X, Y, sampling_method, max_stride, batch_size, add_noise, random_rotate, cutoff):
        
        
        ip = 0
        tot_len = 0
        patches = None
        while tot_len < batch_size:
            
            if sampling_method in ["grid", "random-fixed-width"]:
                p_tmp = Patches(X.shape, initialize_by = sampling_method, \
                                  patch_size = self.model_size, \
                                  stride = max_stride, \
                                  n_points = batch_size)    

            elif sampling_method in ["random"]:
                p_tmp = Patches(X.shape, initialize_by = sampling_method, \
                                  min_patch_size = self.model_size, \
                                  max_stride = max_stride, \
                                  n_points = batch_size)    
            
            y_tmp = p_tmp.extract(Y, self.model_size)[...,np.newaxis]
            ystd = np.std(y_tmp, axis = (1,2,3))
            
            cond_list = ystd > np.max(ystd)*cutoff
            if np.sum(cond_list) > 0:
                # do stuff
                p_tmp = p_tmp.filter_by_condition(cond_list)

                if patches is None:
                    patches = p_tmp.copy()
                else:
                    patches.append(p_tmp)
                    tot_len = len(patches)
            
        patches = patches.select_random_sample(batch_size)
        
        y = patches.extract(Y, self.model_size)[...,np.newaxis]            
        x = patches.extract(X, self.model_size)[...,np.newaxis]
        std_batch = np.random.uniform(0, add_noise, batch_size)
        x = y + np.asarray([np.random.normal(0, std_batch[ii], y.shape[1:]) for ii in range(batch_size)])

        if random_rotate:
            nrots = np.random.randint(0, 4, batch_size)
            for ii in range(batch_size):
                axes = tuple(np.random.choice([0, 1, 2], size=2, replace=False))
                x[ii, ..., 0] = np.rot90(x[ii, ..., 0], k=nrots[ii], axes=axes)
                y[ii, ..., 0] = np.rot90(y[ii, ..., 0], k=nrots[ii], axes=axes)
#         print("DEBUG: shape x %s, shape y %s"%(str(x.shape), str(y.shape)))
        
        return x, y
    
    
#     def _get_xy(self, X, Y, sampling_method, max_stride, batch_size, add_noise, random_rotate):
        
        
#         if sampling_method in ["grid", "random-fixed-width"]:
#             patches = Patches(X.shape, initialize_by = sampling_method, \
#                               patch_size = self.model_size, \
#                               stride = max_stride, \
#                               n_points = batch_size)    

#         elif sampling_method in ["random"]:
#             patches = Patches(X.shape, initialize_by = sampling_method, \
#                               min_patch_size = self.model_size, \
#                               max_stride = max_stride, \
#                               n_points = batch_size)    

#         y = patches.extract(Y, self.model_size)[...,np.newaxis]
#         x = patches.extract(X, self.model_size)[...,np.newaxis]
#         std_batch = np.random.uniform(0, add_noise, batch_size)
#         x = y + np.asarray([np.random.normal(0, std_batch[ii], y.shape[1:]) for ii in range(batch_size)])

#         if random_rotate:
#             nrots = np.random.randint(0, 4, batch_size)
#             for ii in range(batch_size):
#                 axes = tuple(np.random.choice([0, 1, 2], size=2, replace=False))
#                 x[ii, ..., 0] = np.rot90(x[ii, ..., 0], k=nrots[ii], axes=axes)
#                 y[ii, ..., 0] = np.rot90(y[ii, ..., 0], k=nrots[ii], axes=axes)
# #         print("DEBUG: shape x %s, shape y %s"%(str(x.shape), str(y.shape)))
        
#         return x, y
    
    def data_generator(self, Xs, Ys, batch_size, sampling_method, max_stride = 1, random_rotate = False, add_noise = 0.1, cutoff = 0.0):

        
        '''
        
        Parameters  
        ----------  
        vol : np.array  
            Volume from which patches are extracted.  
        batch_size : int  
            Size of the batch generated at every iteration.  
        sampling_method : str  
            Possible methods include "random", "random-fixed-width", "grid"  
        max_stride : int  
            If method is "random" or "multiple-grids", then max_stride is required.  
        
        '''
        
        while True:
            
            n_vols = len(Xs)
            # sample volumes
            # use _get_xy
            idx_vols = np.random.randint(0, n_vols, batch_size)
            
            xy = []
            for ivol in range(n_vols):
                xy.append(self._get_xy_noblanks(Xs[ivol], \
                                                Ys[ivol], \
                                                sampling_method, \
                                                max_stride, \
                                                np.sum(idx_vols == ivol), \
                                                add_noise, \
                                                random_rotate, \
                                                cutoff))
            
            yield np.concatenate([xy[ivol][0] for ivol in range(n_vols)], axis = 0, dtype = 'float32'), \
            np.concatenate([xy[ivol][1] for ivol in range(n_vols)], axis = 0, dtype = 'uint8')
                
    def sparse_segment(X):
        raise NotImplementedError("not implemented")
        
    def _edge_map(self, Y):
        
        '''
        this algorithm was inspired by: https://github.com/tomochallenge/tomochallenge_utils/blob/master/foam_phantom_utils.py
        '''
        msk = np.zeros_like(Y)
        tmp = Y[:-1]!=Y[1:]
        msk[:-1][tmp] = 1
        msk[1:][tmp] = 1
        tmp = Y[:,:-1]!=Y[:,1:]
        msk[:,:-1][tmp] = 1
        msk[:,1:][tmp] = 1
        tmp = Y[:,:,:-1]!=Y[:,:,1:]
        msk[:,:,:-1][tmp] = 1
        msk[:,:,1:][tmp] = 1
        return msk > 0
        
    def _segment_patches(self, X, Y, patches, upsample = 1, arr_split_infer = 1):
        
        t0 = time.time()
        x = patches.extract(X, self.model_size)
        x = x[...,np.newaxis]
        x = np.array_split(x, arr_split_infer)
        
        y_pred = []
        print("\n")
        for jj in range(len(x)):
            tmp = self.models["segmenter"].predict(x[jj])
            tmp = np.round(tmp).astype(np.uint8)
            if upsample > 1:
                tmp = UpSampling3D(size=upsample)(tmp)
            tmp = tmp[...,0]                    
            y_pred.append(tmp)
            print("\rDone %2d of %2d"%(jj+1, len(x)), end = "")
        print("\n")
        y_pred = np.concatenate(y_pred, axis = 0)
        
        s = patches.slices()
        for idx in range(len(patches.points)):
                Y[s[idx,0], s[idx,1], s[idx,2]] = y_pred[idx,...]
        tot_time = time.time() - t0
        print("Total time for segmentation at stride %i: %.2f seconds"%(upsample, tot_time))
        return Y

        
    
    
#     def sparse_segment(self, X, max_stride = 4):
#         '''
#         '''
#         X = self._normalize_volume(X)
#         # first pass at max_stride
#         patches = Patches(X.shape, initialize_by = "grid", \
#                           patch_size = self.model_size, stride = max_stride)
        
        
#         # convert to sobel map
#         Y_edge = self._edge_map(Y_coarse)
        
#         # extract patches to compute if it contains any edge voxels
#         new_patch_size = tuple(np.asarray(self.model_size)//max_stride)
#         p_sel = Patches(new_vol_shape, initialize_by = "grid", patch_size = new_patch_size, stride = 1)
#         sub_vols = p_sel.extract(Y_edge, new_patch_size)        
#         p_sel.add_features(np.std(sub_vols, axis = (1,2,3)) > 0, names = ['has_edges'])
#         sum_ = np.sum(sub_vols, axis = (1,2,3))
#         size_ = np.prod(sub_vols.shape[1:])
#         p_sel.add_features(sum_ == size_, names = ['has_ones'])
#         p_sel.add_features(sum_ == 0, names = ['has_zeros'])
        
#         # rescale patches to original size
#         p_sel = p_sel.rescale(stride, X.shape)
#         len1 = len(p_sel.points)
        
        
#         Y = np.zeros(X.shape, dtype = np.uint8)

#         # what to do about those volumes not selected in the big Y array?
#         p_ones = p_sel.filter_by_condition(p_sel.features_to_numpy(['has_ones']))
#         s = p_ones.slices()
#         for idx in range(len(p_ones.points)):
#                 Y[s[idx,0], s[idx,1], s[idx,2]] = np.ones(tuple(p_ones.widths[idx,...]), dtype = np.uint8)

#         p_zeros = p_sel.filter_by_condition(p_sel.features_to_numpy(['has_zeros']))
#         s = p_zeros.slices()
#         for idx in range(len(p_zeros.points)):
#                 Y[s[idx,0], s[idx,1], s[idx,2]] = np.ones(tuple(p_zeros.widths[idx,...]), dtype = np.uint8)
        
        
#         # now run predictions on only those patches that were selected
#         p_edges = p_sel.filter_by_condition(p_sel.features_to_numpy(['has_edges']))
#         len0 = len(p_edges.points)
#         Y = self._segment_patches(X, Y, p_edges)
        
#         t_save = (len0-len1)/len0*100.0
#         print("compute time saving %.2f pc"%t_save)
        
#         return Y
    
    
    
    
    
if __name__ == "__main__":
    
    print('just a bunch of functions')
    
