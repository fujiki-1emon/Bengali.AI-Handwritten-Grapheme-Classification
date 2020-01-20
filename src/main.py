import argparse
import datetime as dt
import os
from pathlib import Path
import random
import shutil

import addict
import yaml
import numpy as np
from sklearn import model_selection
import torch
from torch.utils.data import DataLoader

from common import LOGDIR
from dataset import MyDataset as Dataset
import loss
import models
from trainer import training
import utils


def main(args):
    with open(args.config, 'r') as f:
        y = yaml.load(f, Loader=yaml.Loader)
    cfg = addict.Dict(y)
    cfg.general.config = args.config

    # misc
    device = cfg.general.device
    random.seed(cfg.general.random_state)
    os.environ['PYTHONHASHSEED'] = str(cfg.general.random_state)
    np.random.seed(cfg.general.random_state)
    torch.manual_seed(cfg.general.random_state)

    # log
    if cfg.general.expid == '':
        expid = dt.datetime.now().strftime('%Y%m%d%H%M%S')
    else:
        expid = cfg.general.expid
    cfg.general.logdir = str(LOGDIR/expid)
    if not os.path.exists(cfg.general.logdir):
        os.makedirs(cfg.general.logdir)
    os.chmod(cfg.general.logdir, 0o777)
    logger = utils.get_logger(os.path.join(cfg.general.logdir, 'main.log'))
    logger.info(f'Logging at {cfg.general.logdir}')
    logger.info(cfg)
    shutil.copyfile(str(args.config), cfg.general.logdir+'/config.yaml')
    # data
    X_train = np.load(cfg.data.X_train, allow_pickle=True)
    y_train = np.load(cfg.data.y_train, allow_pickle=True)
    logger.info('Loaded X_train, y_train')
    # CV
    kf = model_selection.__dict__[cfg.training.split](
        n_splits=cfg.training.n_splits, shuffle=True, random_state=cfg.general.random_state)  # noqa
    score_list = {'loss': [], 'score': []}
    for fold_i, (train_idx, valid_idx) in enumerate(
        kf.split(X=np.zeros(len(y_train)), y=y_train[:, 0])
    ):
        if fold_i + 1 not in cfg.training.target_folds:
            continue
        X_train_ = X_train[train_idx]
        y_train_ = y_train[train_idx]
        X_valid_ = X_train[valid_idx]
        y_valid_ = y_train[valid_idx]
        train_set = Dataset(X_train_, y_train_, cfg, mode='train')
        valid_set = Dataset(X_valid_, y_valid_, cfg, mode='valid')
        train_loader = DataLoader(
            train_set, batch_size=cfg.training.batch_size, shuffle=True,
            num_workers=cfg.training.n_worker)
        valid_loader = DataLoader(
            valid_set, batch_size=cfg.training.batch_size, shuffle=False,
            num_workers=cfg.training.n_worker)

        # model
        model = models.get_model(cfg=cfg)
        model = model.to(device)
        criterion = loss.get_loss_fn(cfg)
        optimizer = utils.get_optimizer(model.parameters(), config=cfg)
        scheduler = utils.get_lr_scheduler(optimizer, config=cfg)

        best = {'loss': 1e+9, 'score': -1.}
        is_best = {'loss': False, 'score': False}
        for epoch_i in range(1, 1 + cfg.training.epochs):
            for param_group in optimizer.param_groups:
                current_lr = param_group['lr']
            train = training(train_loader, model, criterion, optimizer, config=cfg)
            valid = training(
                valid_loader, model, criterion, optimizer, is_training=False, config=cfg)
            if scheduler is not None:
                scheduler.step()

            is_best['loss'] = valid['loss'] < best['loss']
            is_best['score'] = valid['score'] > best['score']
            if is_best['loss']:
                best['loss'] = valid['loss']
            if is_best['score']:
                best['score'] = valid['score']
            state_dict = {
                'epoch': epoch_i,
                'state_dict': model.state_dict(),
                'loss/valid': valid['loss'],
                'score/valid': valid['score'],
                'optimizer': optimizer.state_dict(),
            }
            utils.save_checkpoint(
                state_dict, is_best, Path(cfg.general.logdir)/f'fold_{fold_i}')

            log = f'[{expid}] Fold {fold_i+1} Epoch {epoch_i}/{cfg.training.epochs} '
            log += f'[loss] {train["loss"]:.4f}/{valid["loss"]:.4f} '
            log += f'[score] {train["score"]:.4f}/{valid["score"]:.4f} '
            log += f'({best["score"]:.4f}) '
            log += f'lr {current_lr:.6f}'
            logger.info(log)

        score_list['loss'].append(best['loss'])
        score_list['score'].append(best['score'])
        if cfg.training.single_fold: break  # noqa

    log = f'[{expid}] '
    log += f'[loss] {cfg.training.n_splits}-fold/mean {np.mean(score_list["loss"]):.4f} '
    log += f'[score] {cfg.training.n_splits}-fold/mean {np.mean(score_list["score"]):.4f} '  # noqa
    logger.info(log)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('config', type=str, default='default.yaml')
    args = parser.parse_args()
    main(args)
