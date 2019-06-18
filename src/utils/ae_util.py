import torch
import torch.nn as nn

from models.autoencoder.base import BaseExtendedModel
from models.autoencoder.input_ae import InputAutoencoder, InputVAE
from models.autoencoder.middle_ae import MiddleAutoencoder
from myutils.common import yaml_util
from utils import module_util


def get_autoencoder(config, device=None):
    autoencoder = None
    ae_config = config['autoencoder']
    ae_type = ae_config['type']
    if ae_type == 'input_ae':
        autoencoder = InputAutoencoder(**ae_config['params'])
    elif ae_type == 'input_vae':
        autoencoder = InputVAE(**ae_config['params'])
    elif ae_type == 'middle_ae':
        autoencoder = MiddleAutoencoder(**ae_config['params'])

    if autoencoder is None:
        raise ValueError('ae_type `{}` is not expected'.format(ae_type))

    if device is None:
        return autoencoder, ae_type

    autoencoder = autoencoder.to(device)
    return autoencoder, ae_type


def extend_model(autoencoder, model, input_shape, device, partition_idx):
    if partition_idx is None or partition_idx == 0:
        return nn.Sequential(autoencoder, model)

    modules = list()
    module = model.module if isinstance(model, nn.DataParallel) else model
    module_util.extract_decomposable_modules(module, torch.rand(1, *input_shape).to(device), modules)
    return BaseExtendedModel(modules[:partition_idx], autoencoder, modules[partition_idx:]).to(device)


def get_extended_model(autoencoder, config, input_shape, device):
    org_model_config = config['org_model']
    model_config = yaml_util.load_yaml_file(org_model_config['config'])
    sub_model_config = model_config['model']
    if sub_model_config['type'] == 'inception_v3':
        sub_model_config['params']['aux_logits'] = False

    model = module_util.get_model(model_config, device)
    module_util.resume_from_ckpt(model, sub_model_config, False)
    return extend_model(autoencoder, model, input_shape, device, org_model_config['partition_idx']), model
