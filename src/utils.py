import time
import numpy as np
import os
import sys
from contextlib import contextmanager


def add_dummy_sphere(bullet_client, radius: float = 0.1, position: list | np.ndarray = [0, 0, 0], orientation: list | np.ndarray = [0, 0, 0], color: list | np.ndarray = [1, 0, 0, 1], with_frame: bool = True) -> tuple:
    sphere_id = bullet_client.createVisualShape(
        shapeType=bullet_client.GEOM_SPHERE,
        radius=radius,
        rgbaColor=color,
    )

    sphere_body_id = bullet_client.createMultiBody(
        baseMass=0,
        baseVisualShapeIndex=sphere_id,
        basePosition=position,
        baseOrientation=bullet_client.getQuaternionFromEuler(orientation) if len(orientation) == 3 else orientation,
    )

    if with_frame:
        line_ids = add_coordinate_system(bullet_client, position, bullet_client.getQuaternionFromEuler(orientation) if len(orientation) == 3 else orientation)
    else:
        line_ids = []

    return sphere_body_id, line_ids


def add_dummy_box(bullet_client, half_extents: list = [0.1, 0.1, 0.1], position: list = [0, 0, 0], orientation: list = [0, 0, 0], color: list = [1, 0, 0, 1]) -> tuple:
    box_id = bullet_client.createVisualShape(
        shapeType=bullet_client.GEOM_BOX,
        halfExtents=half_extents,
        rgbaColor=color,
    )

    box_body_id = bullet_client.createMultiBody(
        baseMass=0,
        baseVisualShapeIndex=box_id,
        basePosition=position,
        baseOrientation=bullet_client.getQuaternionFromEuler(orientation),
    )

    line_ids = add_coordinate_system(bullet_client, position, bullet_client.getQuaternionFromEuler(orientation))

    return box_body_id, line_ids


def add_coordinate_system(bullet_client, position: list | np.ndarray, orientation: list | np.ndarray, scale: float = 0.1) -> list:
    # apply quaternion orientation to x, y, z vectors
    x = np.array([scale, 0, 0])
    y = np.array([0, scale, 0])
    z = np.array([0, 0, scale])
    rotation_matrix = np.array(bullet_client.getMatrixFromQuaternion(orientation)).reshape(3, 3)
    x = np.dot(rotation_matrix, x)
    y = np.dot(rotation_matrix, y)
    z = np.dot(rotation_matrix, z)
    # draw the orientation
    x_line_id = bullet_client.addUserDebugLine(position, position + x, [1, 0, 0])
    y_line_id = bullet_client.addUserDebugLine(position, position + y, [0, 1, 0])
    z_line_id = bullet_client.addUserDebugLine(position, position + z, [0, 0, 1])
    return [x_line_id, y_line_id, z_line_id]


def add_coordinate_frame(bullet_client, body_id: int, frame_id: int, size: float = 0.1, line_width: float = 1.0) -> None:
    bullet_client.addUserDebugLine(
        lineFromXYZ=[0, 0, 0],
        lineToXYZ=[size, 0, 0],
        lineColorRGB=[1, 0, 0],
        parentObjectUniqueId=body_id,
        parentLinkIndex=frame_id,
        lineWidth=line_width,
    )
    bullet_client.addUserDebugLine(
        lineFromXYZ=[0, 0, 0],
        lineToXYZ=[0, size, 0],
        lineColorRGB=[0, 1, 0],
        parentObjectUniqueId=body_id,
        parentLinkIndex=frame_id,
        lineWidth=line_width,
    )
    bullet_client.addUserDebugLine(
        lineFromXYZ=[0, 0, 0],
        lineToXYZ=[0, 0, size],
        lineColorRGB=[0, 0, 1],
        parentObjectUniqueId=body_id,
        parentLinkIndex=frame_id,
        lineWidth=line_width,
    )


class PSM:
    def __init__(
        self,
        bullet_client,
        urdf_path: str,
        show_frames: bool = False,
        base_position: list = [0, 0, 0],
        base_orientation: list = [0, 0, 0],
        max_motor_force: float = 1000.0,
        mimic_joint_force_factor: float = 100.0,
    ) -> None:
        self.bullet_client = bullet_client

        self.urdf_path = urdf_path
        with suppress_stdout():
            self.robot_id = self.bullet_client.loadURDF(
                urdf_path,
                useFixedBase=True,
                basePosition=base_position,
                baseOrientation=self.bullet_client.getQuaternionFromEuler(base_orientation),
            )

        for j in range(self.bullet_client.getNumJoints(self.robot_id)):
            self.bullet_client.changeDynamics(self.robot_id, j, linearDamping=0, angularDamping=0)

        self.ee_link_index = 7

        # 0 -> yaw
        # 1 -> pitch
        # 4 -> main insertion
        # 5 -> tool roll
        # 6 -> tool pitch
        # 7 -> tool yaw
        # 9 -> gripper opening -> manual mimic to 8
        self.joint_ids = [0, 1, 4, 5, 6, 7, 9]
        self.joint_names = ["yaw", "pitch", "main_insertion", "tool_roll", "tool_pitch", "tool_yaw", "gripper_opening"]
        self.joint_limits = [
            [-1.605, 1.5994],
            [-0.93556, 0.94249],
            [0.0, 0.24],
            [-3.14159, 3.14159],
            [-1.5708, 1.5708],
            [-1.5708, 1.5708],
            [0.0, 1.0],
        ]

        self.max_motor_force = max_motor_force
        self.mimic_joint_force_factor = mimic_joint_force_factor

        if show_frames:
            for i in range(self.bullet_client.getNumJoints(self.robot_id)):
                add_coordinate_frame(self.bullet_client, self.robot_id, i, size=0.1, line_width=1.0)

    def show_ee_frame(self):
        add_coordinate_frame(self.bullet_client, self.robot_id, self.ee_link_index, size=0.1, line_width=1.0)

    def clip_joint_position(self, joint: int, position: float):
        lower_limit, upper_limit = self.joint_limits[joint]
        return max(lower_limit, min(upper_limit, position))

    def set_joint_position(self, joint: int, position: float):
        joint_id = self.joint_ids[joint]
        clipped_position = self.clip_joint_position(joint, position)

        # restrict tool yaw, pith, and gripper opening, when the tool is still in the insertion shaft
        if joint in (4, 5, 6, 7):
            if self.get_joint_position(2) < 0.05:
                clipped_position = np.clip(clipped_position, np.deg2rad(-10), np.deg2rad(10))

        # if position != clipped_position:
        #     print(f"Joint {joint} position clipped from {position} to {clipped_position}.")

        self.bullet_client.setJointMotorControl2(
            self.robot_id,
            joint_id,
            self.bullet_client.POSITION_CONTROL,
            targetPosition=clipped_position,
            force=self.max_motor_force,
        )

        # Gripper jaw mimic joint
        if joint_id == 9:
            self.bullet_client.setJointMotorControl2(
                self.robot_id,
                8,
                self.bullet_client.POSITION_CONTROL,
                targetPosition=-clipped_position,
                force=self.mimic_joint_force_factor * self.max_motor_force,
            )

        # Pitch mimic joints
        if joint_id == 1:
            for mimic_joint_id, direction in zip((2, 3, 11, 12), (-1, 1, -1, 1)):
                self.bullet_client.setJointMotorControl2(
                    self.robot_id,
                    mimic_joint_id,
                    self.bullet_client.POSITION_CONTROL,
                    targetPosition=direction * clipped_position,
                    force=self.mimic_joint_force_factor * self.max_motor_force,
                )

    def set_joint_positions(self, positions: list | np.ndarray):
        if not len(positions) == 7:
            raise ValueError(f"The number of joint positions should be 7. Got {len(positions)} instead.")

        for i, position in enumerate(positions):
            self.set_joint_position(i, position)

    def reset_joint_position(self, joint: int, position: float):
        joint_id = self.joint_ids[joint]
        self.bullet_client.resetJointState(self.robot_id, joint_id, position)

    def reset_joint_positions(self, positions: list):
        if not len(positions) == 7:
            raise ValueError(f"The number of joint positions should be 7. Got {len(positions)} instead.")

        for i, position in enumerate(positions):
            self.reset_joint_position(i, position)

    def get_joint_positions(self) -> np.ndarray:
        joint_positions = []
        for joint_id in self.joint_ids:
            joint_positions.append(self.bullet_client.getJointState(self.robot_id, joint_id)[0])

        return np.array(joint_positions)

    def get_joint_position(self, joint: int) -> float:
        joint_id = self.joint_ids[joint]
        return self.bullet_client.getJointState(self.robot_id, joint_id)[0]

    def get_joint_velocities(self) -> np.ndarray:
        joint_velocities = []
        for joint_id in self.joint_ids:
            joint_velocities.append(self.bullet_client.getJointState(self.robot_id, joint_id)[1])

        return np.array(joint_velocities)

    def get_joint_velocity(self, joint: int) -> float:
        joint_id = self.joint_ids[joint]
        return self.bullet_client.getJointState(self.robot_id, joint_id)[1]

    def get_ee_pose(self) -> tuple:
        ee_position, ee_orientation = self.bullet_client.getLinkState(self.robot_id, self.ee_link_index, computeForwardKinematics=True)[4:6]
        return np.array(ee_position), np.array(ee_orientation)

    def get_rcm_position(self) -> np.ndarray:
        rcm_position = self.bullet_client.getLinkState(self.robot_id, 13)[0]
        return np.array(rcm_position)

    def demo_motion(self, simulation_hz: int = 500):
        for i in range(7):
            joint_values = []
            joint_values += np.linspace(0.0, self.joint_limits[i][0], int(3.0 * simulation_hz)).tolist()
            joint_values += np.linspace(self.joint_limits[i][0], self.joint_limits[i][1], int(3.0 * simulation_hz)).tolist()
            joint_values += np.linspace(self.joint_limits[i][1], 0.0, int(3.0 * simulation_hz)).tolist()

            for val in joint_values:
                joint_states = np.zeros(7).tolist()
                joint_states[i] = val
                self.set_joint_positions(joint_states)
                self.bullet_client.stepSimulation()
                time.sleep(1 / simulation_hz)

    def tool_position_ik(self, target_position: np.ndarray) -> np.ndarray:
        if not isinstance(target_position, np.ndarray):
            target_position = np.array(target_position)

        rcm_position = self.get_rcm_position()
        relative_vector = target_position - rcm_position

        x, y, z = relative_vector

        insertion = np.sqrt(x * x + y * y + z * z)
        yaw = 0.0
        pitch = 0.0
        yaw = np.sign(x) * np.pi - np.arctan2(x, z)
        pitch = -np.arcsin(y / insertion)

        return np.array([yaw, pitch, insertion])


def print_contact_data_verbose(contact_points: list):
    for contact in contact_points:
        for val, name in zip(contact, ["contactFlag", "bodyUniqueIdA", "bodyUniqueIdB", "linkIndexA", "linkIndexB", "positionOnA", "positionOnB", "contactNormalOnB", "contactDistance", "normalForce", "lateralFriction1", "lateralFrictionDir1", "lateralFriction2", "lateralFrictionDir2"]):
            print(f"{name}: {val}")


def print_link_names(bullet_client, robot_id: int):
    for i in range(bullet_client.getNumJoints(robot_id)):
        name = bullet_client.getJointInfo(robot_id, i)[1]
        print(f"Link {i}: {name}")


def print_joint_info(bullet_client, robot_id: int):
    for i in range(bullet_client.getNumJoints(robot_id)):
        full_info = bullet_client.getJointInfo(robot_id, i)

        for val, name in zip(
            full_info,
            ["jointIndex", "jointName", "jointType", "qIndex", "uIndex", "flags", "jointDamping", "jointFriction", "jointLowerLimit", "jointUpperLimit", "jointMaxForce", "jointMaxVelocity", "linkName", "jointAxis", "parentFramePos", "parentFrameOrn", "parentIndex"],
        ):
            if name == "jointType":
                choices = {
                    bullet_client.JOINT_REVOLUTE: "REVOLUTE",
                    bullet_client.JOINT_PRISMATIC: "PRISMATIC",
                    bullet_client.JOINT_SPHERICAL: "SPHERICAL",
                    bullet_client.JOINT_PLANAR: "PLANAR",
                    bullet_client.JOINT_FIXED: "FIXED",
                    bullet_client.JOINT_POINT2POINT: "POINT2POINT",
                    bullet_client.JOINT_GEAR: "GEAR",
                }
                val = choices[val]
            print(f"{name}: {val}")
        print()


@contextmanager
def suppress_stdout():
    """https://github.com/bulletphysics/bullet3/issues/2170"""
    fd = sys.stdout.fileno()

    def _redirect_stdout(to):
        sys.stdout.close()  # + implicit flush()
        os.dup2(to.fileno(), fd)  # fd writes to 'to' file
        sys.stdout = os.fdopen(fd, "w")  # Python writes to fd

    with os.fdopen(os.dup(fd), "w") as old_stdout:
        with open(os.devnull, "w") as file:
            _redirect_stdout(to=file)
        try:
            yield  # allow code to be run with the redirected stdout
        finally:
            _redirect_stdout(to=old_stdout)  # restore stdout.
            # buffering and flags such as
            # CLOEXEC may be different
