import chainer

from chainer import cuda
from chainer import links
from chainer import functions
from chainer_chemistry.links import EmbedAtomID
from chainer_chemistry.config import MAX_ATOMIC_NUM
from chainer_chemistry.models.gwm import GWM


class GraphConvModel(chainer.Chain):
    def __init__(self, in_channels, out_dim, n_layers, update_layer, readout_layer,
                 hidden_dim_super=None, n_atom_types=MAX_ATOMIC_NUM, n_edge_types=4,
                 with_gwm=True, concat_hidden=False, weight_tying=False):
        super(GraphConvModel, self).__init__()

        n_update_layers = 1 if weight_tying else n_layers
        n_readout_layers = n_layers if concat_hidden else 1

        with self.init_scope():
            self.embed = EmbedAtomID(out_size=in_channels, in_size=n_atom_types)
            self.update_layers = chainer.ChainList(
                *[update_layer(in_channels=in_channels, out_channels=in_channels,
                               n_edge_types=n_edge_types)
                  for _ in range(n_update_layers)])
            self.readout_layers = chainer.ChainList(
                *[readout_layer(out_dim=out_dim, in_channels=in_channels)
                  for _ in range(n_readout_layers)])
            if with_gwm:
                self.gwm = GWM(hidden_dim=in_channels, hidden_dim_super=hidden_dim_super,
                               n_layers=n_update_layers)
                self.embed_super = links.Linear(None, out_size=hidden_dim_super)
                self.linear_for_concat_super = links.Linear(in_size=None, out_size=out_dim)

        self.n_layers = n_layers
        self.weight_tying = weight_tying
        self.with_gwm = with_gwm
        self.concat_hidden = concat_hidden

    def __call__(self, atom_array, adj, super_node=None, is_real_node=None):
        self.reset_state()

        if atom_array.dtype == self.xp.int32:
            h = self.embed(atom_array)
        else:
            h = atom_array

        h0 = functions.copy(h, cuda.get_device_from_array(h.data).id)
        if self.with_gwm:
            h_s = self.embed_super(super_node)

        g_list = []
        print(h.array.shape, adj.shape)
        for step in range(self.n_layers):
            print(step)
            update_layer_index = 0 if self.weight_tying else step
            h2 = self.update_layers[update_layer_index](h, adj)

            if self.with_gwm:
                h, h_s = self.gwm(h, h2, h_s, update_layer_index)

            if self.concat_hidden:
                g = self.readout_layers[step](
                    h=h, h0=h0, is_real_node=is_real_node)
                g_list.append(g)

        if self.concat_hidden:
            return functions.concat(g_list, axis=1)
        else:
            g = self.readout_layers[0](
                h=h, h0=h0, is_real_node=is_real_node)
            if self.with_gwm:
                g = functions.concat((g, h_s), axis=1)
                g = functions.relu(self.linear_for_concat_super(g))
            return g

    def reset_state(self):
        if hasattr(self.update_layers[0], 'reset_state'):
            [update_layer.reset_state() for update_layer in self.update_layers]

        if self.with_gwm:
            self.gwm.GRU_super.reset_state()
            self.gwm.GRU_local.reset_state()
