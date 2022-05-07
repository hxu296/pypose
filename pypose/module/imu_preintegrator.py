import torch
import pypose as pp
from torch import nn


class IMUPreintegrator(nn.Module):
    r"""
    Applies preintegration over IMU input signals.

    IMU updates from duration (:math:`\delta t`), angular rate (:math:`\omega`),
    linear acceleration (:math:`\mathbf{a}`) in body frame, as well as their
    measurement covariance for angular rate :math:`C_{\omega}` and acceleration
    :math:`C_{\mathbf{a}}`. Known IMU rotation :math:`R` estimation can also be provided
    for better precision.

    Args:
        position (torch.Tensor, optional): initial postion. Default: torch.zeros(3)
        rotation (pypose.SO3, optional): initial rotation. Default: :math:`pypose.identity_SO3`
        velocity (torch.Tensor, optional): initial postion. Default: torch.zeros(3)
        gravity (float, optional): the gravity acceleration. Default: 9.81007
    """
    def __init__(self, position = torch.zeros(3),
                       rotation = pp.identity_SO3(),
                       velocity = torch.zeros(3),
                       gravity = 9.81007):
        super().__init__()
        # Initial status of IMU: (pos)ition, (rot)ation, (vel)ocity, (cov)ariance
        self.register_buffer('gravity', torch.tensor([0, 0, gravity]))
        self.register_buffer('pos', position.clone())
        self.register_buffer('rot', rotation.clone())
        self.register_buffer('vel', velocity.clone())
        self.register_buffer('cov', torch.zeros(9,9))
        # Note that cov[9x9] order is rot, vel, pos
        self.register_buffer('_dp', torch.zeros(3))
        self.register_buffer('_dv', torch.zeros(3))
        self.register_buffer('_dr', pp.identity_SO3())
        self.register_buffer('_dt', torch.zeros(1))

    def reset(self):
        self._dp.zero_()
        self._dv.zero_()
        self._dr.identity_()
        self._dt.zero_()

    def update(self, dt, ang, acc, rot:pp.SO3=None, ang_cov=None, acc_cov=None):
        r"""
        IMU Preintegration from duration (dt), angular rate (ang), linear acceleration (acc)
        Uncertainty propagation from measurement covariance (cov): ang_cov, acc_cov
        Known IMU rotation (rot) estimation can be provided for better precision
        
        IMU Measurements Propagation:
        
        .. math::
            \begin{align*}
                {\Delta}R_{ik+1} &= {\Delta}R_{ik} \mathrm{Exp} ((w_k - b_i^g) {\Delta}t) \\
                {\Delta}v_{ik+1} &= {\Delta}v_{ik} + {\Delta}R_{ik} (a_k - b_i^a) {\Delta}t  \\
                {\Delta}p_{ik+1} &= {\Delta}v_{ik} + {\Delta}v_{ik} {\Delta}t + 1/2 {\Delta}R_{ik} (a_k - b_i^a) {\Delta}t^2
            \end{align*}

        where:

            - :math:`{\Delta}R_{ik}` is the preintegrated rotation between the :math:`i`-th and :math:`k`-th time step.

            - :math:`{\Delta}v_{ik}` is the preintegrated velocity between the :math:`i`-th and :math:`k`-th time step.

            - :math:`{\Delta}p_{ik}` is the preintegrated position between the :math:`i`-th and :math:`k`-th time step.

            - :math:`a_k` is linear acceleration at the :math:`k`-th time step.

            - :math:`w_k` is angular rate at the :math:`k`-{th} time step.

            
        Uncertainty Propagation:

        .. math::
            \begin{align*}
                B &= [B_g, B_a]                              \\
                C_{ik+1} &= A C_{ik} A^T + B \mathrm{diag}(C_g, C_a) B^T \\
                  &= A C A^T + B_g C_g B_g^T + B_a C_a B_a^T
            \end{align*},

        .. math::
            A = \begin{bmatrix}
                    {\Delta}R_{ik+1}^T & 0_{3*3} \\
                    -{\Delta}R_{ik} (a_k - b_i^g)^\wedge {\Delta}t & I_{3*3} & 0_{3*3} \\
                    -1/2{\Delta}R_{ik} (a_k - b_i^g)^\wedge {\Delta}t^2 & I_{3*3} {\Delta}t & I_{3*3}
                \end{bmatrix},

        .. math::
            B_g = \begin{bmatrix}
                    J_r^k \Delta t  \\
                    0_{3*3}  \\
                    0_{3*3} 
                \end{bmatrix},

            B_a = \begin{bmatrix}
                    0_{3*3} \\
                    {\Delta}R_{ik} {\Delta}t  \\
                    1/2 {\Delta}R_{ik} {\Delta}t^2
                \end{bmatrix},

        where :math:`\cdot^\wedge` is the skew matrix (:meth:`pypose.vec2skew`),
        :math:`C \in\mathbf{R}^{9\times 9}` is the covarience matrix,
        and :math:`J_r^k` is the right jacobian (:meth:`pypose.Jr`) of integrated rotation
        :math:`\mathrm{Exp}(w_k{\Delta}t)` at :math:`k`-th time step.            

        Args:
            dt (torch.Tensor): time interval from last update. :obj:`shape`: (1)
            ang (torch.Tensor): angular rate (:math:`\omega`) in IMU body frame. :obj:`shape`: (3)
            acc (torch.Tensor): linear acceleration (:math:`\mathbf{a}`) in IMU body frame.
                :obj:`shape`: (3)
            rot (pypose.SO3, optional): known IMU rotation, see following example. :obj:`lshape`: (1)
            ang_cov (torch.Tensor, optional): covariance matrix of angular rate. :obj:`shape`: (3, 3).
                Default: :obj:`torch.eye(3)*(1.6968*10**-4)**2` (Adapted from Euroc dataset)
            acc_cov (torch.Tensor, optional): covariance matrix of linear acceleration.
                :obj:`shape`: (3, 3). Default: :obj:`torch.eye(3)*(2.0*10**-3)**2`  (Adapted from
                Euroc dataset)

        Example:

            >>> p = torch.zeros(3)
            >>> r = pp.identity_SO3()
            >>> v = torch.zeros(3)


            >>> # Preintegrator Initialisation
            >>> integrator = pp.module.IMUPreintegrator(p,r,v)
            >>> ang = torch.tensor([0.1,0.1,0.1])
            >>> acc = torch.tensor([0.1,0.1,0.1])
            >>> rot = pp.mat2SO3(torch.eye(3))
            >>> dt = torch.tensor([0.002])

            >>> # Update Function
            >>> integrator.update(dt, ang, acc, rot)
            >>> integrator.update(dt, ang, acc, rot)

            >>> # Forward Function
            >>> print(integrator()['vel'])
            tensor([ 0.0004,  0.0004, -0.0388])


        Example:

            .. list-table:: 

                * - .. figure:: /_static/img/module/imu/imu-known-rot.png
                        :width: 300

                    Fig. 1. Known Rotation.

                  - .. figure:: /_static/img/module/imu/imu-unknown-rot.png
                        :width: 300

                    Fig. 2. Estimated Rotation.

        Refer to Eq. (A9), (A10), (A7), (A8) in
        `this RSS paper <https://rpg.ifi.uzh.ch/docs/RSS15_Forster_Supplementary.pdf>`_ for more details.
        """
        dr = pp.so3(ang*dt).Exp()
        if isinstance(rot, pp.LieTensor):
            a = acc - rot.Inv() @ self.gravity
        else:
            a = acc - (self.rot * self._dr * dr).Inv() @ self.gravity
        self._dp = self._dp + self._dv * dt + self._dr @ a * 0.5 * dt**2
        self._dv = self._dv + self._dr @ a * dt
        self._dr = self._dr * dr
        self._dt = self._dt + dt

        if ang_cov is None: # gyro covariance
            Cg = torch.eye(3, device=dt.device, dtype=dt.dtype) * (1.6968*10**-4)**2
        if acc_cov is None: # acc covariance
            Ca = torch.eye(3, device=dt.device, dtype=dt.dtype) * (2.0*10**-3)**2

        Ha = pp.vec2skew(acc)
        A = torch.eye(9, device=dt.device, dtype=dt.dtype)
        A[0:3, 0:3] = dr.matrix().mT
        A[3:6, 0:3] = - self._dr.matrix() @ Ha * dt
        A[6:9, 0:3] = - 0.5 * self._dr.matrix() @ Ha * dt**2
        A[6:9, 3:6] = torch.eye(3, device=dt.device, dtype=dt.dtype) * dt

        Bg = torch.zeros(9, 3, device=dt.device, dtype=dt.dtype)
        Bg[0:3, 0:3] = pp.so3(ang*dt).Jr() * dt
        Ba = torch.zeros(9, 3, device=dt.device, dtype=dt.dtype)
        Ba[3:6, 0:3] = self._dr.matrix() * dt
        Ba[6:9, 0:3] = 0.5 * self._dr.matrix() * dt**2

        self.cov = A @ self.cov @ A.mT + Bg @ Cg @ Bg.mT / dt + Ba @ Ca @ Ba.mT / dt

    def forward(self, reset=True):
        r"""
        Propagated IMU status.

        .. math::
            \begin{align*}
                R_j &= {\Delta}R_{ij} * R_i                \\
                v_j &= {\Delta}v_{ij} * R_i   + v_i + g \Delta t_{ij} \\
                p_j &= {\Delta}p_{ij} * R_i   + p_i + v_i \Delta t_{ij} + 1/2 g \Delta t_{ij}^2 \\
                
            \end{align*} 


        Args:
            reset (bool, optional): if reset the preintegrator to initial state. Default: :obj:`True`

        Returns:
            :obj:`dict`: A :class:`dict` containing 4 items: 'rot'ation, 'vel'ocity, 'pos'ition, and 'cov'ariance.

            - 'rot' (pypose.SO3): rotation. :obj:`lshape`: (1)

            - 'vel' (torch.Tensor): velocity. :obj:`shape`: (3)

            - 'pos' (torch.Tensor): postion. :obj:`shape`: (3)

            - 'cov' (torch.Tensor): covariance (order: rotation, velocity, position). :obj:`shape`: (9, 9)

        IMU State Propagation :
        
        
        
        
        
        Note:
            Output covariance (Shape: (9, 9)) is in the order of rotation, velocity, and position.

        Refer to Eq. (38) in `this TRO paper <http://rpg.ifi.uzh.ch/docs/TRO16_forster.pdf>`_ for more details.
        """
        self.pos = self.pos + self.rot @ self._dp + self.vel * self._dt
        self.vel = self.vel + self.rot @ self._dv
        self.rot = self.rot * self._dr
        if reset is True:
            self.reset()
        return {'rot':self.rot.clone(),
                'vel':self.vel.clone(),
                'pos':self.pos.clone(),
                'cov':self.cov.clone()}
