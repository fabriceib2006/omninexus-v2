# ════════════════════════════════════════════════════════════════
# OMNINEXUS — brain/autoencoder.py
# Regime Detection Autoencoder
# Trained on normal market conditions
# Reconstruction error IS the anomaly signal
# High error = regime change incoming
# Deploys on Azure ML free tier
# ════════════════════════════════════════════════════════════════

import logging
import json
import os
import numpy as np
from datetime import datetime
from typing import Optional
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import config

logger = logging.getLogger('omninexus.brain.autoencoder')

# ── MODEL FILES ────────────────────────────────────────────────
MODEL_DIR       = 'logs/autoencoder'
WEIGHTS_FILE    = f'{MODEL_DIR}/weights.json'
HISTORY_FILE    = f'{MODEL_DIR}/training_history.json'
SCALER_FILE     = f'{MODEL_DIR}/scaler_params.json'

# ── FEATURE DEFINITION ─────────────────────────────────────────
# These are the input features the autoencoder learns from
# Order matters — must be consistent between train and inference
FEATURE_NAMES = [
    'real_yield',        # US 10Y real yield
    'friction_score',    # Geopolitical friction 0-100
    'gold_bias',         # Gold bias score 0-100
    'gbp_bias',          # GBP bias score 0-100
    'boe_boj_spread',    # BoE/BoJ yield spread
    'session_score',     # Session volatility score
    'dark_pool_gold',    # Dark pool Z-score for gold
    'dark_pool_gbp',     # Dark pool Z-score for GBP
    'behavioral_gold',   # Gold behavioral exhaust 0-100
    'behavioral_gbp',    # GBP behavioral exhaust 0-100
]

FEATURE_DIM = len(FEATURE_NAMES)


# ── SIMPLE AUTOENCODER (NumPy — no PyTorch needed) ─────────────
# Uses a simple linear autoencoder with tanh activations
# Encoder: 10 → 5 → 3
# Decoder: 3 → 5 → 10
# Reconstruction error = anomaly score

class NumpyAutoencoder:
    """
    Lightweight autoencoder implemented in pure NumPy.
    No PyTorch or TensorFlow required.
    Runs efficiently on Azure ML free compute tier.

    Architecture:
      Encoder: 10 → 5 → 3 (bottleneck)
      Decoder:  3 → 5 → 10 (reconstruction)
    """

    def __init__(self, input_dim: int = FEATURE_DIM):
        self.input_dim  = input_dim
        self.hidden_dim = 5
        self.latent_dim = 3

        # Initialize weights randomly
        np.random.seed(42)
        self.W1 = np.random.randn(input_dim, self.hidden_dim) * 0.1
        self.W2 = np.random.randn(self.hidden_dim, self.latent_dim) * 0.1
        self.W3 = np.random.randn(self.latent_dim, self.hidden_dim) * 0.1
        self.W4 = np.random.randn(self.hidden_dim, input_dim) * 0.1

        self.b1 = np.zeros(self.hidden_dim)
        self.b2 = np.zeros(self.latent_dim)
        self.b3 = np.zeros(self.hidden_dim)
        self.b4 = np.zeros(input_dim)

        self.is_trained = False

    def _tanh(self, x):
        return np.tanh(x)

    def _tanh_grad(self, x):
        return 1.0 - np.tanh(x) ** 2

    def encode(self, x: np.ndarray) -> np.ndarray:
        """Encodes input to latent representation."""
        h1 = self._tanh(x @ self.W1 + self.b1)
        z  = self._tanh(h1 @ self.W2 + self.b2)
        return z

    def decode(self, z: np.ndarray) -> np.ndarray:
        """Decodes latent representation back to input space."""
        h3   = self._tanh(z @ self.W3 + self.b3)
        recon = h3 @ self.W4 + self.b4
        return recon

    def reconstruct(self, x: np.ndarray) -> np.ndarray:
        """Full forward pass: encode then decode."""
        z = self.encode(x)
        return self.decode(z)

    def reconstruction_error(self, x: np.ndarray) -> float:
        """
        Calculates mean squared reconstruction error.
        This IS the anomaly score.
        High error = market state unlike anything in training.
        """
        recon = self.reconstruct(x)
        mse   = np.mean((x - recon) ** 2)
        return float(mse)

    def train(
        self,
        X: np.ndarray,
        epochs: int = 500,
        lr: float = 0.01
    ) -> list:
        """
        Trains the autoencoder using gradient descent.
        X shape: (n_samples, input_dim)
        Returns list of loss values per epoch.
        """
        losses = []

        for epoch in range(epochs):
            epoch_loss = 0.0

            for i in range(len(X)):
                x = X[i]

                # Forward pass
                h1 = self._tanh(x @ self.W1 + self.b1)
                z  = self._tanh(h1 @ self.W2 + self.b2)
                h3 = self._tanh(z @ self.W3 + self.b3)
                recon = h3 @ self.W4 + self.b4

                # Loss (MSE)
                loss = np.mean((x - recon) ** 2)
                epoch_loss += loss

                # Backward pass (gradient descent)
                d_recon = 2 * (recon - x) / len(x)

                # W4, b4
                d_W4 = np.outer(h3, d_recon)
                d_b4 = d_recon
                d_h3 = d_recon @ self.W4.T

                # W3, b3
                d_h3_act = d_h3 * self._tanh_grad(
                    z @ self.W3 + self.b3
                )
                d_W3 = np.outer(z, d_h3_act)
                d_b3 = d_h3_act
                d_z  = d_h3_act @ self.W3.T

                # W2, b2
                d_z_act = d_z * self._tanh_grad(
                    h1 @ self.W2 + self.b2
                )
                d_W2 = np.outer(h1, d_z_act)
                d_b2 = d_z_act
                d_h1 = d_z_act @ self.W2.T

                # W1, b1
                d_h1_act = d_h1 * self._tanh_grad(
                    x @ self.W1 + self.b1
                )
                d_W1 = np.outer(x, d_h1_act)
                d_b1 = d_h1_act

                # Update weights
                self.W1 -= lr * d_W1
                self.b1 -= lr * d_b1
                self.W2 -= lr * d_W2
                self.b2 -= lr * d_b2
                self.W3 -= lr * d_W3
                self.b3 -= lr * d_b3
                self.W4 -= lr * d_W4
                self.b4 -= lr * d_b4

            avg_loss = epoch_loss / len(X)
            losses.append(avg_loss)

            if epoch % 100 == 0:
                logger.info(
                    f'Autoencoder epoch {epoch}: '
                    f'loss={avg_loss:.6f}'
                )

        self.is_trained = True
        return losses

    def save_weights(self, filepath: str):
        """Saves model weights to JSON."""
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        weights = {
            'W1': self.W1.tolist(),
            'W2': self.W2.tolist(),
            'W3': self.W3.tolist(),
            'W4': self.W4.tolist(),
            'b1': self.b1.tolist(),
            'b2': self.b2.tolist(),
            'b3': self.b3.tolist(),
            'b4': self.b4.tolist(),
            'is_trained': self.is_trained,
        }
        with open(filepath, 'w') as f:
            json.dump(weights, f)
        logger.info(f'Autoencoder weights saved: {filepath}')

    def load_weights(self, filepath: str) -> bool:
        """Loads model weights from JSON."""
        try:
            with open(filepath, 'r') as f:
                weights = json.load(f)
            self.W1 = np.array(weights['W1'])
            self.W2 = np.array(weights['W2'])
            self.W3 = np.array(weights['W3'])
            self.W4 = np.array(weights['W4'])
            self.b1 = np.array(weights['b1'])
            self.b2 = np.array(weights['b2'])
            self.b3 = np.array(weights['b3'])
            self.b4 = np.array(weights['b4'])
            self.is_trained = weights.get('is_trained', True)
            logger.info(f'Autoencoder weights loaded: {filepath}')
            return True
        except Exception as e:
            logger.error(f'Weight load error: {e}')
            return False


# ── SCALER ─────────────────────────────────────────────────────
class MinMaxScaler:
    """
    Simple min-max scaler.
    Normalizes features to 0-1 range.
    """

    def __init__(self):
        self.min_vals = None
        self.max_vals = None
        self.fitted   = False

    def fit(self, X: np.ndarray):
        self.min_vals = X.min(axis=0)
        self.max_vals = X.max(axis=0)
        self.fitted   = True

    def transform(self, X: np.ndarray) -> np.ndarray:
        if not self.fitted:
            return X
        range_vals = self.max_vals - self.min_vals
        range_vals[range_vals == 0] = 1.0
        return (X - self.min_vals) / range_vals

    def fit_transform(self, X: np.ndarray) -> np.ndarray:
        self.fit(X)
        return self.transform(X)

    def save(self, filepath: str):
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        params = {
            'min_vals': self.min_vals.tolist()
            if self.min_vals is not None else None,
            'max_vals': self.max_vals.tolist()
            if self.max_vals is not None else None,
            'fitted': self.fitted,
        }
        with open(filepath, 'w') as f:
            json.dump(params, f)

    def load(self, filepath: str) -> bool:
        try:
            with open(filepath, 'r') as f:
                params = json.load(f)
            self.min_vals = np.array(params['min_vals']) \
                if params['min_vals'] else None
            self.max_vals = np.array(params['max_vals']) \
                if params['max_vals'] else None
            self.fitted = params['fitted']
            return True
        except Exception:
            return False


# ── REGIME DETECTOR ────────────────────────────────────────────
class AutoencoderRegimeDetector:
    """
    Main regime detection system.
    Wraps NumpyAutoencoder with training data generation,
    anomaly scoring, and regime classification.
    """

    def __init__(self):
        self.model     = NumpyAutoencoder(FEATURE_DIM)
        self.scaler    = MinMaxScaler()
        self.threshold = config.AUTOENCODER_THRESHOLD
        self.error_history = []

        # Try to load existing weights
        self._load_if_exists()

    def _load_if_exists(self):
        """Loads saved weights if available."""
        if (os.path.exists(WEIGHTS_FILE) and
                os.path.exists(SCALER_FILE)):
            self.model.load_weights(WEIGHTS_FILE)
            self.scaler.load(SCALER_FILE)
            logger.info('Loaded existing autoencoder weights')

    def generate_training_data(
        self,
        n_samples: int = 1000
    ) -> np.ndarray:
        """
        Generates synthetic normal market data for training.
        Uses realistic ranges for each feature.
        In production, replace with real historical data.
        """
        np.random.seed(42)

        # Normal market ranges for each feature
        # [real_yield, friction, gold_bias, gbp_bias,
        #  boe_boj_spread, session_score,
        #  dark_pool_gold, dark_pool_gbp,
        #  behavioral_gold, behavioral_gbp]

        data = np.column_stack([
            np.random.normal(1.5, 0.8, n_samples),    # real_yield
            np.random.normal(35, 12, n_samples),       # friction
            np.random.normal(50, 15, n_samples),       # gold_bias
            np.random.normal(50, 15, n_samples),       # gbp_bias
            np.random.normal(2.5, 0.8, n_samples),     # boe_boj_spread
            np.random.normal(50, 20, n_samples),       # session_score
            np.random.normal(0.0, 1.0, n_samples),     # dark_pool_gold
            np.random.normal(0.0, 1.0, n_samples),     # dark_pool_gbp
            np.random.normal(25, 10, n_samples),       # behavioral_gold
            np.random.normal(25, 10, n_samples),       # behavioral_gbp
        ])

        # Clip to realistic ranges
        data[:, 0] = np.clip(data[:, 0], -2.0, 5.0)
        data[:, 1] = np.clip(data[:, 1], 0.0, 100.0)
        data[:, 2] = np.clip(data[:, 2], 0.0, 100.0)
        data[:, 3] = np.clip(data[:, 3], 0.0, 100.0)
        data[:, 4] = np.clip(data[:, 4], -1.0, 6.0)
        data[:, 5] = np.clip(data[:, 5], 0.0, 100.0)
        data[:, 6] = np.clip(data[:, 6], -3.0, 3.0)
        data[:, 7] = np.clip(data[:, 7], -3.0, 3.0)
        data[:, 8] = np.clip(data[:, 8], 0.0, 100.0)
        data[:, 9] = np.clip(data[:, 9], 0.0, 100.0)

        return data

    def fit(
        self,
        training_data: np.ndarray = None,
        epochs: int = 500
    ) -> dict:
        """
        Trains the autoencoder on normal market data.
        Call once, then use score() for inference.
        """
        logger.info('Training Regime Autoencoder...')

        if training_data is None:
            training_data = self.generate_training_data()

        # Scale data
        X_scaled = self.scaler.fit_transform(training_data)

        # Train
        losses = self.model.train(X_scaled, epochs=epochs)

        # Calculate threshold from training reconstruction errors
        errors = [
            self.model.reconstruction_error(X_scaled[i])
            for i in range(len(X_scaled))
        ]
        mean_error = np.mean(errors)
        std_error  = np.std(errors)

        # Threshold = mean + 2*std (95th percentile)
        self.threshold = float(mean_error + 2 * std_error)

        # Save weights
        self.model.save_weights(WEIGHTS_FILE)
        self.scaler.save(SCALER_FILE)

        # Save history
        history = {
            'trained_at':    datetime.utcnow().isoformat(),
            'n_samples':     len(training_data),
            'epochs':        epochs,
            'final_loss':    losses[-1],
            'mean_error':    float(mean_error),
            'std_error':     float(std_error),
            'threshold':     self.threshold,
        }

        os.makedirs(MODEL_DIR, exist_ok=True)
        with open(HISTORY_FILE, 'w') as f:
            json.dump(history, f, indent=2)

        logger.info(
            f'Autoencoder trained: '
            f'loss={losses[-1]:.6f} | '
            f'threshold={self.threshold:.6f}'
        )

        return history

    def build_observation(
        self,
        real_yield:      float = 0.0,
        friction_score:  float = 35.0,
        gold_bias:       float = 50.0,
        gbp_bias:        float = 50.0,
        boe_boj_spread:  float = 2.5,
        session_score:   float = 50.0,
        dark_pool_gold:  float = 0.0,
        dark_pool_gbp:   float = 0.0,
        behavioral_gold: float = 25.0,
        behavioral_gbp:  float = 25.0,
    ) -> np.ndarray:
        """
        Builds a feature vector from current signal values.
        Called with live data from signal aggregators.
        """
        return np.array([
            real_yield,
            friction_score,
            gold_bias,
            gbp_bias,
            boe_boj_spread,
            session_score,
            dark_pool_gold,
            dark_pool_gbp,
            behavioral_gold,
            behavioral_gbp,
        ])

    def score(
        self,
        observation: np.ndarray
    ) -> float:
        """
        Calculates reconstruction error for an observation.
        Higher score = more anomalous = regime change risk.
        """
        if not self.model.is_trained:
            logger.warning(
                'Model not trained. '
                'Run fit() first. Returning 0.0'
            )
            return 0.0

        if self.scaler.fitted:
            obs_scaled = self.scaler.transform(
                observation.reshape(1, -1)
            )[0]
        else:
            obs_scaled = observation

        error = self.model.reconstruction_error(obs_scaled)
        self.error_history.append(error)

        return error

    def detect_anomaly(
        self,
        observation: np.ndarray
    ) -> dict:
        """
        Full anomaly detection result.
        Returns score, is_anomaly flag, and regime assessment.
        """
        error = self.score(observation)

        is_anomaly = error >= self.threshold

        # Regime classification
        if error < self.threshold * 0.5:
            regime     = 'STABLE'
            confidence = 'HIGH'
            emoji      = '🟢'
        elif error < self.threshold:
            regime     = 'TRANSITIONING'
            confidence = 'MODERATE'
            emoji      = '🟡'
        elif error < self.threshold * 1.5:
            regime     = 'ANOMALY_DETECTED'
            confidence = 'LOW'
            emoji      = '🟠'
        else:
            regime     = 'EXTREME_ANOMALY'
            confidence = 'VERY_LOW'
            emoji      = '🔴'

        grey_zone = (
            self.threshold * 0.8 <= error < self.threshold
        )

        result = {
            'reconstruction_error': round(error, 6),
            'threshold':            round(self.threshold, 6),
            'is_anomaly':           is_anomaly,
            'regime':               regime,
            'confidence':           confidence,
            'emoji':                emoji,
            'grey_zone':            grey_zone,
            'error_ratio':          round(
                error / self.threshold, 3
            ) if self.threshold > 0 else 0,
            'timestamp':            datetime.utcnow().isoformat(),
        }

        if is_anomaly:
            logger.warning(
                f'REGIME ANOMALY: error={error:.6f} '
                f'>= threshold={self.threshold:.6f} | '
                f'Regime: {regime}'
            )
        else:
            logger.info(
                f'Regime stable: error={error:.6f} | '
                f'{regime}'
            )

        return result


# ── DIRECT TEST ────────────────────────────────────────────────
if __name__ == '__main__':
    print('\n' + '='*55)
    print('OMNINEXUS — Autoencoder Regime Detector Test')
    print('='*55 + '\n')

    detector = AutoencoderRegimeDetector()

    if not detector.model.is_trained:
        print('Training autoencoder on synthetic data...')
        history = detector.fit(epochs=300)
        print(f'Training complete:')
        print(f'  Final loss:  {history["final_loss"]:.6f}')
        print(f'  Threshold:   {history["threshold"]:.6f}')
        print(f'  Mean error:  {history["mean_error"]:.6f}')
    else:
        print('Loaded existing trained model')

    print('\nTesting normal market conditions:')
    normal_obs = detector.build_observation(
        real_yield     = 1.5,
        friction_score = 35.0,
        gold_bias      = 52.0,
        gbp_bias       = 48.0,
        boe_boj_spread = 2.5,
        session_score  = 50.0,
    )
    result = detector.detect_anomaly(normal_obs)
    print(f'  Reconstruction Error: {result["reconstruction_error"]}')
    print(f'  Threshold:            {result["threshold"]}')
    print(f'  Is Anomaly:           {result["is_anomaly"]}')
    print(f'  Regime:               {result["regime"]} {result["emoji"]}')

    print('\nTesting anomalous conditions (crisis simulation):')
    crisis_obs = detector.build_observation(
        real_yield     = -1.5,
        friction_score = 92.0,
        gold_bias      = 95.0,
        gbp_bias       = 10.0,
        boe_boj_spread = -0.5,
        session_score  = 90.0,
        dark_pool_gold = 3.8,
    )
    result2 = detector.detect_anomaly(crisis_obs)
    print(f'  Reconstruction Error: {result2["reconstruction_error"]}')
    print(f'  Threshold:            {result2["threshold"]}')
    print(f'  Is Anomaly:           {result2["is_anomaly"]}')
    print(f'  Regime:               {result2["regime"]} {result2["emoji"]}')
    print(f'  Grey Zone:            {result2["grey_zone"]}')