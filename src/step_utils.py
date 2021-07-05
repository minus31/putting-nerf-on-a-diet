import jax
import jax.numpy as np
from jax import jit, random


def render_fn(rnd_input, model, params, bvals, rays, near, far, N_samples, rand):
    chunk = 5
    for i in range(0, rays.shape[1], chunk):
        out = render_fn_inner(rnd_input, model, params, bvals, rays[:, i:i + chunk], near, far, rand, True, N_samples)
        if i == 0:
            rets = out
        else:
            rets = [np.concatenate([a, b], 0) for a, b in zip(rets, out)]
    return rets


def render_fn_inner(rnd_input, model, params, bvals, rays, near, far, rand, allret, N_samples):
    return render_rays(rnd_input, model, params, bvals, rays, near, far,
                       N_samples=N_samples, rand=rand, allret=allret)


def render_rays(rnd_input, model, params,
                bvals, rays, near, far,
                N_samples, rand=False, allret=False):
    rays_o, rays_d = rays

    # Compute 3D query points
    z_vals = np.linspace(near, far, N_samples)
    if rand:
        z_vals += random.uniform(rnd_input, shape=list(rays_o.shape[:-1]) + [N_samples]) * (far - near) / N_samples
    # r(t) = o + t*d
    pts = rays_o[..., None, :] + rays_d[..., None, :] * z_vals[..., :, None]

    # Run network
    pts_flat = np.reshape(pts, [-1, 3])
    if bvals is not None:
        pts_flat = np.concatenate([np.sin(pts_flat @ bvals.T),
                                   np.cos(pts_flat @ bvals.T)], axis=-1)

    raw = model.apply(params, pts_flat)
    raw = np.reshape(raw, list(pts.shape[:-1]) + [4])

    # Compute opacities and colors
    rgb, sigma_a = raw[..., :3], raw[..., 3]
    sigma_a = jax.nn.relu(sigma_a)
    rgb = jax.nn.sigmoid(rgb)

    # Do volume rendering
    dists = np.concatenate([z_vals[..., 1:] - z_vals[..., :-1], np.broadcast_to([1e10], z_vals[..., :1].shape)], -1)
    alpha = 1. - np.exp(-sigma_a * dists)
    trans = np.minimum(1., 1. - alpha + 1e-10)
    trans = np.concatenate([np.ones_like(trans[..., :1]), trans[..., :-1]], -1)
    weights = alpha * np.cumprod(trans, -1)

    rgb_map = np.sum(weights[..., None] * rgb, -2)
    acc_map = np.sum(weights, -1)

    if not allret:
        return rgb_map

    depth_map = np.sum(weights * z_vals, -1)

    return rgb_map, depth_map, acc_map

@jit
def single_step(self, rng, image, rays, params, bds, inner_step_size, N_samples):
    def sgd(param, update):
        return param - inner_step_size * update

    rng, rng_inputs = jax.random.split(rng)

    def loss_model(params):
        g = self.render_rays(rng_inputs, self.model, params, None, rays, bds[0], bds[1], N_samples, rand=True)
        return mse_fn(g, image)

    model_loss, grad = jax.value_and_grad(loss_model)(params)
    new_params = jax.tree_multimap(sgd, params, grad)
    return rng, new_params, model_loss


# optimize render_fn_inner by JIT (func in, func out)
render_fn_inner = jit(render_fn_inner, static_argnums=(1, 7, 8, 9))
mse_fn = jit(lambda x, y: np.mean((x - y)**2))
psnr_fn = jit(lambda x, y: -10 * np.log10(mse_fn(x, y)))