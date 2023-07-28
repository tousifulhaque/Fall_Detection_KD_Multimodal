import argparse
import yaml
import torch
import traceback
import random 
import numpy as np 
import sys
import time
import torch.nn as nn
import torch.optim as optim
from tqdm import tqdm
import warnings
import json

def parse_args():

    parser = argparse.ArgumentParser(description = 'Distillation')
    parser.add_argument('--config' , default = './config/utd/student.yaml')

    #training
    parser.add_argument('--batch-size', type = int, default = 16, metavar = 'N',
                        help = 'input batch size for training (default: 8)')

    parser.add_argument('--test-batch-size', type = int, default = 8, 
                        metavar = 'N', help = 'input batch size for testing(default: 1000)')

    parser.add_argument('--epochs', type = int , default = 70, metavar = 'N', 
                        help = 'number of epochs to train (default: 10)')

    #optim
    parser.add_argument('--base-lr', type = float, default = 0.001, metavar = 'LR', 
                        help = 'learning rate (default: 0.001)')
    parser.add_argument('--weight-decay', type = float , default=0.0004)

    #model
    parser.add_argument('--model' ,default= None, help = 'Name of Model to load')

    #model args
    # parser.add_argument('--mocap-frames', default=600, type = int, help='Skeleton length')
    # parser.add_argument('--acc-frames', default=256, type = int, help = 'Accelerometer length')
    # parser.add_argument('--num-joints', default=31, type = int, help = 'Num joints in skeleton')
    # parser.add_argument('--num-classes', default=11, type = int) 
    # parser.add_argument('--acc-embed', default=32, type =int, help = 'Acceleromter embedding' )
    # parser.add_argument('--adepth', default = 4, type = int)
    # parser.add_argument('--num-heads', default= 4, type = int)]
    parser.add_argument('--device', nargs='+', default=[0], type = int)
    parser.add_argument('--model-args', default= str, help = 'A dictionary for model args')
    # parser.add_argument('--no-cuda', action = 'store_true', default = False, 
    #                     help = 'disables CUDA training')

    #dataloader 
    parser.add_argument('--feeder', default= None , help = 'Dataloader location')
    parser.add_argument('--train-feeder-args',default=str, help = 'A dict for dataloader args' )
    parser.add_argument('--val-feeder-args', default=str , help = 'A dict for validation data loader')
    parser.add_argument('--test_feeder_args',default=str, help= 'A dict for test data loader')
    
    parser.add_argument('--seed', type =  int , default = 2 , help = 'random seed (default: 1)') 

    parser.add_argument('--log-interval', type = int , default = 10, metavar = 'N',
                        help = 'how many bathces to wait before logging training status')
    parser.add_argument('--val-batch-size', type = int, default = 32, metavar = 'N', 
                        help = 'Validation batch sieze (default : 8)')
    parser.add_argument('--dataset', type = str, default= 'ncrc', metavar = 'D', help = 'Which dataset to use')

   
    parser.add_argument('--work-dir', type = str, default = 'simple', metavar = 'F', help = "Working Directory")
    parser.add_argument('--print-log',type=str2bool,default=True,help='print logging or not')
    return parser

def str2bool(v):
    if v.lower() in ('yes', 'true', 't', 'y', '1'):
        return True
    elif v.lower() in ('no', 'false', 'f', 'n', '0'):
        return False
    else:
        raise argparse.ArgumentTypeError('Unsupported value encountered.')
    
def init_seed(seed):
    torch.cuda.manual_seed_all(seed)
    torch.manual_seed(seed)
    np.random.seed(seed)
    random.seed(seed)
    # torch.backends.cudnn.enabled = True
    # training speed is too slow if set to True
    torch.backends.cudnn.deterministic = False

    # on cuda 11 cudnn8, the default algorithm is very slow
    # unlike on cuda 10, the default works well
    torch.backends.cudnn.benchmark = True

def import_class(import_str):
    mod_str , _sep, class_str = import_str.rpartition('.')
    __import__(mod_str)
    try:
        return getattr(sys.modules[mod_str], class_str)
    except AttributeError:
        raise ImportError('Class %s cannot be found (%s)' % (class_str, traceback.format_exception(*sys.exc_info())))

class Trainer():
    
    def __init__(self, arg):
        self.arg = arg
        # self.save_arg()
        self.load_model()
    
    def load_model(self):
        use_cuda = torch.cuda.is_available()
        self.output_device = self.arg.device[0] if type(self.arg.device) is list else self.arg.device
        Model = import_class(self.arg.model)
        self.model = Model(**self.arg.model_args).to(f'cuda:{self.output_device}' if use_cuda else 'cpu')
        self.loss = nn.CrossEntropyLoss().cuda(self.output_device)
    
    def load_optimizer(self):
        
        if self.arg.optimizer == "Adam" :
            self.optimizer = optim.Adam(
                self.model.parameters(), 
                lr = self.arg.base_lr,
                weight_decay=self.arg.weight_decay
            )
        elif self.arg.optimizer == "AdamW":
            self.optimizer = optim.AdamW(
                self.model.parameters(), 
                lr = self.arg.base_lr, 
                weight_decay=self.arg.weight_decay
            )
        
        else :
           raise ValueError()
        
    def load_data(self):
        Feeder = import_class(self.arg.feeder)
        self.data_loader = dict()
        if self.arg.phase == 'train':
            self.data_loader['train'] = torch.utils.data.DataLoader(
                dataset=Feeder(**self.arg.train_feeder_args),
                batch_size=self.arg.batch_size,
                shuffle=True,
                pin_memory=True,
                prefetch_factor=16,
                num_workers=self.arg.num_worker,
                drop_last=True,
                worker_init_fn=init_seed)
            
            self.data_loader['val'] = torch.utils.data.DataLoader(
                dataset=Feeder(**self.arg.val_feeder_args),
                batch_size=self.arg.batch_size,
                shuffle=True,
                pin_memory=True,
                prefetch_factor=16,
                num_workers=self.arg.num_worker,
                drop_last=True,
                worker_init_fn=init_seed)
        self.data_loader['test'] = torch.utils.data.DataLoader(
            dataset=Feeder(**self.arg.test_feeder_args),
            batch_size=self.arg.test_batch_size,
            shuffle=False,
            num_workers=self.arg.num_worker,
            drop_last=False,
            worker_init_fn=init_seed)

    def record_time(self):
        self.cur_time = time.time()
        return self.cur_time

    def split_time(self):
        split_time = time.time() - self.cur_time
        self.record_time()
        return split_time

    def print_log(self, string, print_time = True):
        print(string)
        if arg.print_log:
            with open('{}/log.txt'.format(self.arg.work_dir), 'a') as f:
                print(string, file = f)

    def train(self, epoch, save_model = False):
        self.model.train()
        self.record_time()
        loader = self.data_loader['train']
        timer = dict(dataloader = 0.001, model = 0.001, statistics = 0.001)
        loss_value = []
        acc_value = []
        accuracy = 0
        cnt = 0
        train_loss = 0

        process = tqdm(loader, ncols = 40)

        for batch_idx, (inputs, targets) in enumerate(process):
            with torch.no_grad():
                inputs = inputs.cuda(self.output_device) #print("Input batch: ",inputs)
                targets = targets.cuda(self.output_device)
            
            timer['dataloader'] += self.split_time()

            self.optimizer.zero_grad()

            # Ascent Step
            #print("labels: ",targets)
            out, logits,predictions = self.model(inputs.float())
            #print("predictions: ",torch.argmax(predictions, 1) )
            loss = self.criterion(logits, targets)
            loss.mean().backward()
            self.optimizer.step()

            timer['model'] += self.split_time()
            with torch.no_grad():
                train_loss += loss.sum().item()
                accuracy += (torch.argmax(predictions, 1) == targets).sum().item()
            cnt += len(targets)
            train_loss /= cnt
            accuracy *= 100. / cnt 
            time['statistics'] += self.split_time()
        loss_value.append(train_loss)
        acc_value.append[accuracy] 

        proportion = {
            k: '{:02d}%'.format(int(round(v * 100 / sum(timer.values()))))
            for k, v in timer.items()
        }
        self.print_log(
            '\tTraining Loss: {:4f}. Training Acc: {:2f}%'.format(train_loss, accuracy)
        )
        self.print_log('\tTime consumption: [Data]{dataloader}, [Network]{model}'.format(**proportion))

        #Still need to work with this one
        if save_model:
            state_dict = self.model.state_dict()
            weights = OrderedDict([[k.split('module.')[-1], v.cpu()] for k, v in state_dict.items()])

            torch.save(weights, self.arg.model_saved_name + '-' + str(epoch+1) + '-' + str(int(self.global_step)) + '.pt')

    # def save_arg(self):
    #     #save arg
    #     arg_dict = vars(self.arg)


if __name__ == "__main__":
    parser = parse_args()

    # load arg form config file
    p = parser.parse_args()
    if p.config is not None:
        with open(p.config, 'r') as f:
            default_arg = yaml.safe_load(f)
        key = vars(p).keys()
        for k in default_arg.keys():
            if k not in key:
                print('WRONG ARG: {}'.format(k))
                assert (k in key)
        parser.set_defaults(**default_arg)

    arg = parser.parse_args()
    init_seed(arg.seed)
    Trainer(arg)