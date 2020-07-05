import argparse

from data import HistoDataNorm
from resnet import BasicResNet

from sklearn.metrics import cohen_kappa_score
import random
import torch.optim.lr_scheduler as sched
import torch.backends.cudnn as cudnn
import util
from tqdm import tqdm

import torch
import torch.utils.data as data_utils
import torchvision
import torch.nn as nn
import torch.optim as optim
import torchvision.transforms.functional

import os

import numpy as np
import shutil

def main(args, seed):

    # Set up main device and scale batch size
    device = 'cuda' if torch.cuda.is_available() and args.gpu_ids else 'cpu'
    # args.batch_size *= max(1, len(args.gpu_ids))
    # print(args.batch_size)




    # Set random seeds
    print("seed", seed)
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True

    domain_list_train = ["C116P77ThinF", "C132P93ThinF", "C137P98ThinF", "C180P141NThinF", "C182P143NThinF", \
                         "C184P145ThinF", "C39P4thinF", 'C59P20thinF', "C68P29N", "C99P60ThinF"]

    if args.mode=='generations':

        experiment_folder = args.experiment_folder + str(seed) +'/'
        print(experiment_folder)
        os.makedirs(experiment_folder, exist_ok=True)
        
        dataset = HistoDataNorm(experiment_folder+'generations/', domain_list=domain_list_train, augmentation=True)
        num_classes = 2
        num_domain_classes = 10
        

    else:
        # domain_list_train = os.listdir('dataset_sorted_by_domain/')
        # num_classes = 2
        # num_domain_classes = 201
        
        experiment_folder = 'two_headed_classification/' + str(seed) +'/'
        print(experiment_folder)
        os.makedirs(experiment_folder, exist_ok=True)
        
        domain_list_train = ["C116P77ThinF", "C132P93ThinF", "C137P98ThinF", "C180P141NThinF", "C182P143NThinF", \
                         "C184P145ThinF", "C39P4thinF", 'C59P20thinF', "C68P29N", "C99P60ThinF"]
        dataset = HistoDataNorm('dataset_sorted_by_domain/', domain_list=domain_list_train, augmentation=True)
        num_classes = 2
        num_domain_classes = 10
        



    train_size = int(0.8 * len(dataset))
    test_size = len(dataset) - train_size
    train_dataset, test_dataset = torch.utils.data.random_split(dataset, [train_size, test_size])

    print("training on", len(train_dataset))
    print("validating on", len(test_dataset))

    trainloader = data_utils.DataLoader(train_dataset, batch_size=args.batch_size, shuffle=True, num_workers=args.num_workers)

    testloader = data_utils.DataLoader(test_dataset, batch_size=len(test_dataset), shuffle=False, num_workers=args.num_workers)


    print("num_classes", num_classes)
    print("num_domain_classes", num_domain_classes)
    # Model
    print('Building model..')
    net = BasicResNet(num_classes=num_classes, network='resnet50', pretrained=True, num_domain_classes=num_domain_classes)
    net = net.to(device)

    if device == 'cuda':
        net = torch.nn.DataParallel(net, args.gpu_ids)
        cudnn.benchmark = args.benchmark

    start_epoch = 0
    if args.resume:
        # Load checkpoint.
        print('Resuming from checkpoint at ckpts/best.pth.tar...')
        assert os.path.isdir(experiment_folder + 'ckpts'), 'Error: no checkpoint directory found!'
        checkpoint = torch.load(experiment_folder + 'ckpts/best.pth.tar')
        net.load_state_dict(checkpoint['net'])
        global best_loss
        global global_step
        best_loss = checkpoint['test_loss']
        start_epoch = checkpoint['epoch']
        global_step = start_epoch * len(domain_list_train)

    loss_fn = nn.CrossEntropyLoss()
    optimizer = optim.Adam(net.parameters(), lr=args.lr)
    scheduler = sched.LambdaLR(optimizer, lambda s: min(1., s / args.warm_up))

    train_accuracy_class, train_accuracy_domain =0.0, 0.0
    test_accuracy_class, test_accuracy_domain =0.0, 0.0
    kappa_class, kappa_domain = None, None
    for epoch in range(start_epoch, start_epoch + args.num_epochs):
        new_train_accuracy_class, new_train_accuracy_domain = train(epoch, net, trainloader, device, optimizer, scheduler,
              loss_fn, args.max_grad_norm)
        new_test_accuracy_class, new_test_accuracy_domain, save, kappa_class, kappa_domain = test(epoch, net, testloader, device, loss_fn, args.batch_size, experiment_folder=experiment_folder)


        if save ==True:
            train_accuracy_class, train_accuracy_domain = new_train_accuracy_class, new_train_accuracy_domain
            test_accuracy_class, test_accuracy_domain = new_test_accuracy_class, new_test_accuracy_domain

    record1 = "Kappa class " + str(kappa_class) + " Validation class " + str(test_accuracy_class) + " - train accuracy class" + str(train_accuracy_class)
    record2 = "Kappa domain" + str(kappa_domain) + "Validation domain " + str(test_accuracy_domain) + " - train accuracy domain" + str(train_accuracy_domain)
    print(record1)
    print(record2)
    file = open(experiment_folder + "2H_classification_validation_results.txt", "a+")
    file.write(record1)
    file.write(record2)
    file.close()


@torch.enable_grad()
def train(epoch, net, trainloader, device, optimizer, scheduler, loss_fn, max_grad_norm):
    global global_step
    print('\nEpoch: %d' % epoch)
    net.train()
    loss_meter = util.AverageMeter()
    correct_class=0
    correct_domain=0

    with tqdm(total=len(trainloader.dataset)) as progress_bar:
        for x, y, d, yd in trainloader:
            x , y, d, yd = x.to(device), y.to(device), d.to(device), yd.to(device)
            optimizer.zero_grad()
            z1, z2 = net(x)
            loss2 = loss_fn(z2, d.argmax(dim=1))
            loss1 = loss_fn(z1, y.argmax(dim=1))
            loss_meter.update(loss2.item(), x.size(0))
            loss_meter.update(loss1.item(), x.size(0))
            loss=loss1+loss2
            loss.backward()
            if max_grad_norm > 0:
                util.clip_grad_norm(optimizer, max_grad_norm)
            optimizer.step()
            scheduler.step(global_step)

            progress_bar.set_postfix(nll=loss_meter.avg,
                                     bpd=util.bits_per_dim(x, loss_meter.avg),
                                     lr=optimizer.param_groups[0]['lr'])
            progress_bar.update(x.size(0))
            global_step += x.size(0)
            values_class, pred_class = torch.max(z1, 1)
            values_domain, pred_domain = torch.max(z2, 1)

            correct_class += pred_class.eq(y.argmax(dim=1)).sum().item()
            correct_domain += pred_domain.eq(d.argmax(dim=1)).sum().item()
    accuracy_class = correct_class * 100. / len(trainloader.dataset)
    accuracy_domain = correct_domain* 100. / len(trainloader.dataset)

    print('train accuracy class', accuracy_class)
    print('train accuracy domain', accuracy_domain)

    return accuracy_class, accuracy_domain


@torch.no_grad()
def test(epoch, net, testloader, device, loss_fn, num_samples, experiment_folder):
    global best_loss
    net.eval()
    loss_meter = util.AverageMeter()
    correct_class = 0
    correct_domain = 0
    save =False
    with tqdm(total=len(testloader.dataset)) as progress_bar:
        for x, y, d, yd in testloader:
            x , y, d, yd = x.to(device), y.to(device), d.to(device), yd.to(device)
            z1, z2 = net(x)
            loss2 = loss_fn(z2, d.argmax(dim=1))
            loss1 = loss_fn(z1, y.argmax(dim=1))
            loss_meter.update(loss2.item(), x.size(0))
            loss_meter.update(loss1.item(), x.size(0))
            progress_bar.set_postfix(nll=loss_meter.avg,
                                     bpd=util.bits_per_dim(x, loss_meter.avg))
            progress_bar.update(x.size(0))
            values_class, pred_class = torch.max(z1, 1)
            values_domain, pred_domain = torch.max(z2, 1)

            correct_class += pred_class.eq(y.argmax(dim=1)).sum().item()
            correct_domain += pred_domain.eq(d.argmax(dim=1)).sum().item()

            kappa_class = cohen_kappa_score(y.argmax(dim=1).cpu().numpy(), pred_class.cpu().numpy(), labels=None, weights=None)
            kappa_domain = cohen_kappa_score(d.argmax(dim=1).cpu().numpy(), pred_domain.cpu().numpy(), labels=None, weights=None)
            print("kappa class and domain", kappa_class, kappa_domain)

    # Save checkpoint
    if loss_meter.avg < best_loss:
        save=True
        print('Saving...')
        state = {
            'net': net.state_dict(),
            'test_loss': loss_meter.avg,
            'epoch': epoch,
        }
        os.makedirs(experiment_folder + 'ckpts', exist_ok=True)
        torch.save(state, experiment_folder + 'ckpts/best.pth.tar')
        best_loss = loss_meter.avg

    accuracy_class = correct_class * 100. / len(testloader.dataset)
    accuracy_domain = correct_domain * 100. / len(testloader.dataset)

    print('test accuracy class', accuracy_class)
    print('test accuracy domain', accuracy_domain)

    return accuracy_class, accuracy_domain, save, kappa_class, kappa_domain



if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Conditional Glow on Malaria Dataset')


    def str2bool(s):
        return s.lower().startswith('t')


    parser.add_argument('--batch_size', default=64, type=int, help='Batch size per GPU')
    parser.add_argument('--benchmark', type=str2bool, default=True, help='Turn on CUDNN benchmarking')
    parser.add_argument('--gpu_ids', default=[0], type=eval, help='IDs of GPUs to use')
    parser.add_argument('--lr', default=1e-3, type=float, help='Learning rate')
    parser.add_argument('--max_grad_norm', type=float, default=-1., help='Max gradient norm for clipping')
    parser.add_argument('--num_channels', '-C', default=128, type=int, help='Number of channels in hidden layers')
    parser.add_argument('--num_levels', '-L', default=3, type=int, help='Number of levels in the Glow model')
    parser.add_argument('--num_steps', '-K', default=8, type=int, help='Number of steps of flow in each level')
    parser.add_argument('--num_epochs', default=100, type=int, help='Number of epochs to train')
    parser.add_argument('--num_samples', default=64, type=int, help='Number of samples at test time')
    parser.add_argument('--num_workers', default=8, type=int, help='Number of data loader threads')
    parser.add_argument('--resume', type=str2bool, default=False, help='Resume from checkpoint')
    parser.add_argument('--seed', type=str, default="0,1,2", help='seed for reproducibility')
    parser.add_argument('--warm_up', default=500000, type=int, help='Number of steps for lr warm-up')
    parser.add_argument('--mode', default="real", choices=['real', 'generations'])
    parser.add_argument('--experiment_folder', default="new")

    args = parser.parse_args()
    seed_list = [int(item) for item in args.seed.split(',')]
    print(seed_list)
    for seed in seed_list:
        best_loss = float('inf')
        global_step = 0

        main(args, seed)