import torch 
import torch.nn as nn 
import torch.nn.functional as F
# from model_utils import Attention
from einops import rearrange, repeat
from einops.layers.torch import Rearrange

class Mlp(nn.Module):
    def __init__(self, in_features, hidden_features=128, out_features=64, drop=0.2):
        super().__init__()
        out_features = out_features or in_features
        hidden_features = hidden_features or in_features
        self.fc1 = nn.Linear(out_features, hidden_features)
        self.fc2 = nn.Linear(hidden_features, out_features)
        self.act = nn.GELU()
        self.drop = nn.Dropout(drop)

    def forward(self, x):

        x = self.fc1(x)
        x = self.act(x)
        x = self.drop(x)
        x = self.fc2(x)
        x = self.drop(x)

        return x

class SAttention(nn.Module):
    def __init__(self,dim=64, heads = 3, dim_heads = 64):
        super().__init__()
        inner_dim = dim_heads * heads 
        self.heads = heads
        self.scale = dim_heads ** -0.5
        self.norm = nn.LayerNorm(dim)

        #didn't understand this one
        self.attend = nn.Softmax(dim = -1)

        self.to_qkv = nn.Linear(dim, inner_dim*3, bias = False) #[]
        self.to_out = nn.Linear(inner_dim, dim , bias= False)

    def forward(self, x):
        qkv = self.to_qkv(x).chunk(self.heads, dim = -1) #[batch*64 * 8] [batch*64 * 8 ] [batch*64 * 8]
        q, k , v = map(lambda t : rearrange(t, 'b n (h d) -> b h n d', h = self.heads), qkv)

        dots = torch.matmul(q, k.transpose(-1, -2)) * self.scale
        attn = self.attend(dots)

        out = torch.matmul(attn, v)
        out = rearrange(out, 'b h n d -> b n (h d)')
        return self.to_out(out)

class Attention(nn.Module):
    def __init__(self, dim, num_heads=8, qkv_bias=False, qk_scale=None, attn_drop=0., proj_drop=0.):
        super().__init__()
        self.num_heads = num_heads
        head_dim = dim // num_heads
        # NOTE scale factor was wrong in my original version, can set manually to be compat with prev weights
        self.scale = qk_scale or head_dim ** -0.5

        self.qkv = nn.Linear(dim, dim * 3, bias=qkv_bias)
        self.attn_drop = nn.Dropout(attn_drop)
        self.proj = nn.Linear(dim, dim)
        self.proj_drop = nn.Dropout(proj_drop)

    def forward(self, x):
        B, N, C = x.shape  #Batch x Num of tokens x embed dim
        qkv = self.qkv(x).reshape(B, N, 3, self.num_heads, C // self.num_heads).permute(2, 0, 3, 1, 4)
        q, k, v = qkv[0], qkv[1], qkv[2]   # make torchscript happy (cannot use tensor as tuple)

        attn = (q @ k.transpose(-2, -1)) * self.scale
        attn = attn.softmax(dim=-1)
        attn = self.attn_drop(attn)

        x = (attn @ v).transpose(1, 2).reshape(B, N, C)
        x = self.proj(x)
        x = self.proj_drop(x)
        return x

class Encoder(nn.Module):
    def __init__(self, num_head = 3, acc_dim = 64, acc_frames = 256):
        super().__init__()
        self.acc_dim = acc_dim
        self.num_head = num_head
    #    self.msa = Attention(dim = self.acc_dim, heads = self.num_head, dim_heads = self.acc_dim)
        self.msa = Attention(dim = acc_dim, num_heads=4)
        self.mlp_layer = Mlp(in_features=acc_dim)
        # need to add shape to layernorm 
        self.norm1 = nn.LayerNorm(normalized_shape=self.acc_dim)
        self.norm2 = nn.LayerNorm(normalized_shape=self.acc_dim)

    def forward(self,inputs):
        x = self.norm1(inputs)
        x = self.msa(inputs)
        x  = x + inputs
        x = self.norm2(x)
        res = x
        x = self.mlp_layer(x)
        x = x + res
        return x


class TinyVit(nn.Module):
    def __init__(self,seq_len = 256, patch_size = 16, num_classes = 11, depth = 3, dim = 64, heads = 3, channels = 3, dim_head = 64, dropout = 0.2):
        super().__init__()

        num_patches = seq_len // patch_size # [256/ 16]
        self.patch_dim = channels * patch_size #[ 3 * 16]
        self.num_classes = num_classes
        self.depth = depth

        # +1 is for class embedding
        self.pos_embedding = nn.Parameter(torch.randn(1, num_patches + 1, dim)) #size [1, 16+1, 64]
        self.cls_token = nn.Parameter(torch.randn(1, 1, dim)) #size [1, 1, 64]
        self.head_dim = dim_head
        self.dropout = nn.Dropout(dropout)


        self.patch_embedding = nn.Sequential(
            Rearrange('b (n p) c -> b n (p c)', p = patch_size),  #[32, 16, 16*3]
            nn.LayerNorm(self.patch_dim), 
            nn.Linear(self.patch_dim, dim ), #[32, 16, 64]
            nn.LayerNorm(dim)
        )

        self.encoder_layers = nn.ModuleList([Encoder(num_head=heads, acc_dim=dim_head, acc_frames=seq_len) for i in range(depth)])
        # self.to_latent = nn.Identity()
        self.linear_head =  nn.Sequential(
            nn.LayerNorm(dim),
            nn.Linear(dim, self.num_classes)
        )

    def forward(self, inputs):
            
        x = self.patch_embedding(inputs) #[32, 16, 64]
        # print(f'After patch embedding {x.shape}')
        b, n , _ = x.shape
        cls_token = repeat(self.cls_token, '1 1 d -> b 1 d' ,b = b) #[32, 1, 64]
        x = torch.cat((cls_token, x), dim = 1) #[32, 17, 64]

        # print(f'After class token {x.shape}')

        x = x + self.pos_embedding #[32, 16+1, 64]
        for _, layer in enumerate(self.encoder_layers):
            x = layer(x) #[32, 17, 64]
        
        # print(f'After all 3 layers {x.shape}')
        # print('Before only class')
        # print(x)
        

        x = x[:,0, :] #[32, 1, 64]
        
        # print(f'one class token {x.shape}')
        x = self.linear_head(x) #[32, 1, 11]
        # print(f'After Linear head {x.shape}')
        # x = F.log_softmax(x, dim=1)

        # print(x)
        # print('-----------xx---------')
        return x





