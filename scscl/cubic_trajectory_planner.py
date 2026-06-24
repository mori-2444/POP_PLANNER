import numpy as np


class PolynomialTrajectory(object):
    def __init__(self, position_model, velocity_model, backend_name):
        self.position_model = position_model
        self.velocity_model = velocity_model
        self.backend_name = backend_name

    def position(self, sample_time):
        return float(self.position_model(sample_time))

    def velocity(self, sample_time):
        return float(self.velocity_model(sample_time))


def build_cubic_trajectory(start_position, target_position, start_velocity, end_velocity, duration):
    try:
        from scipy.interpolate import CubicHermiteSpline

        spline = CubicHermiteSpline(
            [0.0, duration],
            [start_position, target_position],
            [start_velocity, end_velocity],
        )
        derivative = spline.derivative()
        return PolynomialTrajectory(spline, derivative, "scipy.interpolate.CubicHermiteSpline")
    except Exception:
        pass

    system = np.array(
        [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [1.0, duration, duration ** 2, duration ** 3],
            [0.0, 1.0, 2.0 * duration, 3.0 * duration ** 2],
        ],
        dtype=float,
    )
    boundary = np.array(
        [start_position, start_velocity, target_position, end_velocity],
        dtype=float,
    )
    coefficients = np.linalg.solve(system, boundary)
    polynomial = np.polynomial.Polynomial(coefficients)
    return PolynomialTrajectory(
        polynomial,
        polynomial.deriv(),
        "numpy.polynomial.Polynomial",
    )


def generate_samples(trajectory, duration, sample_interval):
    sample_times = list(np.arange(0.0, duration, sample_interval))
    if not sample_times or sample_times[-1] < duration:
        sample_times.append(duration)

    samples = []
    for sample_time in sample_times:
        position = trajectory.position(sample_time)
        velocity = trajectory.velocity(sample_time)
        samples.append((sample_time, position, velocity))
    return samples


def build_segment_samples(
    start_position,
    target_position,
    start_velocity,
    end_velocity,
    duration,
    sample_interval,
    time_offset=0.0,
    include_first_point=True,
):
    trajectory = build_cubic_trajectory(
        start_position,
        target_position,
        start_velocity,
        end_velocity,
        duration,
    )
    raw_samples = generate_samples(trajectory, duration, sample_interval)
    if not include_first_point:
        raw_samples = raw_samples[1:]

    samples = []
    for sample_time, position, velocity in raw_samples:
        samples.append((sample_time + time_offset, position, velocity))
    return trajectory.backend_name, samples
