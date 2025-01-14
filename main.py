import argparse
import os
import shutil

import torch.backends.cudnn as cudnn
import torch.nn.parallel
import torch.optim
import torch.utils.data as data
import torchvision.datasets as datasets
# used for logging to TensorBoard
from tensorboard_logger import configure

from train import *

parser = argparse.ArgumentParser(description='PyTorch DenseNet Training')
parser.add_argument('--epochs', default=300, type=int,
                    help='number of total epochs to run')
parser.add_argument('--start-epoch', default=0, type=int,
                    help='manual epoch number (useful on restarts)')
parser.add_argument('-b', '--batch-size', default=64, type=int,
                    help='mini-batch size (default: 64)')
parser.add_argument('--lr', '--learning-rate', default=0.1, type=float,
                    help='initial learning rate')
parser.add_argument('--momentum', default=0.9, type=float, help='momentum')
parser.add_argument('--weight-decay', '--wd', default=1e-4, type=float,
                    help='weight decay (default: 1e-4)')
parser.add_argument('--print-freq', '-p', default=10, type=int,
                    help='print frequency (default: 10)')
parser.add_argument('--layers', default=100, type=int,
                    help='total number of layers (default: 100)')
parser.add_argument('--growth', default=12, type=int,
                    help='number of new channels per layer (default: 12)')
parser.add_argument('--droprate', default=0, type=float,
                    help='dropout probability (default: 0.0)')
parser.add_argument('--no-augment', dest='augment', action='store_false',
                    help='whether to use standard augmentation (default: True)')
parser.add_argument('--reduce', default=0.5, type=float,
                    help='compression rate in transition stage (default: 0.5)')
parser.add_argument('--no-bottleneck', dest='bottleneck', action='store_false',
                    help='To not use bottleneck block')
parser.add_argument('--resume', default='', type=str,
                    help='path to latest checkpoint (default: none)')
parser.add_argument('--imagenet', dest='imagenet', action='store_true',
                    help='To train on ImageNet')
parser.add_argument('--test', default='', type=str,
                    help='path to trained model (default: none)')
parser.add_argument('--name', default='DenseNet_BC_100_12', type=str,
                    help='name of experiment')
parser.add_argument('--tensorboard',
                    help='Log progress to TensorBoard', action='store_true')
parser.set_defaults(bottleneck=True)
parser.set_defaults(augment=True)
parser.set_defaults(imagenet=False)

best_prec1 = 0.
args = Namespace


def main():
    global args, best_prec1
    args = parser.parse_args()
    if args.tensorboard:
        configure(f"runs/{args.name}")

    # Data loading code
    normalize = transforms.Normalize(mean=[0.485, 0.456, 0.406],
                                     std=[0.229, 0.224, 0.225])

    if args.imagenet:
        if args.augment:
            transform_train = transforms.Compose([
                transforms.RandomResizedCrop(224),
                transforms.RandomHorizontalFlip(),
                transforms.ToTensor(),
                normalize,
            ])
        else:
            transform_train = transforms.Compose([
                transforms.ToTensor(),
                normalize,
            ])
        transform_test = transforms.Compose([
            transforms.Resize(256),
            transforms.CenterCrop(224),
            transforms.ToTensor(),
            normalize,
        ])
    else:
        if args.augment:
            transform_train = transforms.Compose([
                transforms.RandomCrop(32, padding=4),
                transforms.RandomHorizontalFlip(),
                transforms.ToTensor(),
                normalize,
            ])
        else:
            transform_train = transforms.Compose([
                transforms.ToTensor(),
                normalize,
            ])
        transform_test = transforms.Compose([
            transforms.ToTensor(),
            normalize
        ])

    # Split dataset in train set and validation set
    if args.imagenet:
        folder = '../data/ILSVRC/Data/CLS-LOC/'
        traindir = os.path.join(folder, 'train')
        valdir = os.path.join(folder, 'val')
        train_ds = datasets.ImageFolder(traindir, transform=transform_train)
        val_ds = datasets.ImageFolder(valdir, transform=transform_test)
    else:  # CIFAR-10
        dataset = datasets.CIFAR10('../data', train=True, download=True, transform=transform_train)
        test_ds = datasets.CIFAR10('../data', train=False, transform=transform_test)
        ds_size = len(dataset)
        val_size = int(0.1 * ds_size)
        train_size = ds_size - val_size
        train_ds, val_ds = data.random_split(dataset, [train_size, val_size])

    kwargs = {'num_workers': 4, 'pin_memory': True}
    train_loader = data.DataLoader(train_ds, batch_size=args.batch_size, shuffle=True, **kwargs)
    val_loader = data.DataLoader(val_ds, batch_size=args.batch_size, shuffle=True, **kwargs)
    if not args.imagenet:
        test_loader = data.DataLoader(test_ds, batch_size=args.batch_size, shuffle=True, **kwargs)

    # create model
    if args.imagenet:
        model = dn.DenseNet4(args.layers, 1000, args.growth, reduction=args.reduce,
                             bottleneck=args.bottleneck, droprate=args.droprate)
    else:
        model = dn.DenseNet3(args.layers, 10, args.growth, reduction=args.reduce,
                             bottleneck=args.bottleneck, droprate=args.droprate)

    # get the number of model parameters
    print(f'Number of model parameters: {sum([p.data.nelement() for p in model.parameters()])}')

    # for training on multiple GPUs. 
    # Use CUDA_VISIBLE_DEVICES=0,1 to specify which GPUs to use
    # model = torch.nn.DataParallel(model).cuda()
    model = model.cuda()

    # optionally resume from a checkpoint
    if args.resume:
        if os.path.isfile(args.resume):
            print(f"=> loading checkpoint '{args.resume}'")
            checkpoint = torch.load(args.resume)
            args.start_epoch = checkpoint['epoch']
            best_prec1 = checkpoint['best_prec1']
            model.load_state_dict(checkpoint['state_dict'])
            print(f"=> loaded checkpoint '{args.resume}' (epoch {checkpoint['epoch']})")
        else:
            print(f"=> no checkpoint found at '{args.resume}'")

    cudnn.benchmark = True

    if args.test and not args.imagenet:
        args.test = f"runs/{args.test}/checkpoint.pth.tar"
        if os.path.isfile(args.test):
            print(f"=> loading model '{args.test}'")
            checkpoint = torch.load(args.test)
            args.start_epoch = checkpoint['epoch']
            best_prec1 = checkpoint['best_prec1']
            model.load_state_dict(checkpoint['state_dict'])
            print(f"=> loaded model '{args.test}'")
            test(test_loader, model, args)
        else:
            print(f"=> no model found at '{args.test}'")
        return

    # define loss function (criterion) and optimizer
    criterion = nn.CrossEntropyLoss().cuda()
    optimizer = torch.optim.SGD(model.parameters(), args.lr,
                                momentum=args.momentum,
                                nesterov=True,
                                weight_decay=args.weight_decay)

    for epoch in range(args.start_epoch, args.epochs):
        adjust_learning_rate(optimizer, epoch)

        # train for one epoch
        train(train_loader, model, criterion, optimizer, epoch, args)

        # evaluate on validation set
        prec1 = validate(val_loader, model, criterion, epoch, args)

        # remember best prec@1 and save checkpoint
        is_best = prec1 > best_prec1
        best_prec1 = max(prec1, best_prec1)
        save_checkpoint({
            'epoch': epoch + 1,
            'state_dict': model.state_dict(),
            'best_prec1': best_prec1,
        }, is_best)
    print('Best accuracy: ', best_prec1)

    if not args.imagenet:
        test(test_loader, model, args)


def save_checkpoint(state: dict, is_best: bool, filename: str = 'checkpoint.pth.tar'):
    """Saves checkpoint to disk"""
    directory = f"runs/{args.name}/"
    if not os.path.exists(directory):
        os.makedirs(directory)
    filename = directory + filename
    torch.save(state, filename)
    if is_best:
        shutil.copyfile(filename, f'runs/{args.name}/model_best.pth.tar')


def adjust_learning_rate(optimizer: torch.optim.SGD, epoch: int):
    """Sets the learning rate to the initial LR decayed by 10 after 150 and 225 epochs"""
    if args.imagenet:
        lr = args.lr * (0.1 ** (epoch // 30)) * (0.1 ** (epoch // 60))
    else:
        lr = args.lr * (0.1 ** (epoch // 150)) * (0.1 ** (epoch // 225))
    # log to TensorBoard
    if args.tensorboard:
        log_value('learning_rate', lr, epoch)
    for param_group in optimizer.param_groups:
        param_group['lr'] = lr


if __name__ == '__main__':
    main()
