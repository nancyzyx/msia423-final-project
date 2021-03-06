import argparse
import logging
import pickle

import numpy as np
import pandas as pd
import sklearn
import yaml
import xgboost

from src.generate_features import choose_features, get_target

logger = logging.getLogger(__name__)

methods = dict(xgboost=xgboost.XGBClassifier)


def split_data(X, y, train_size=1, test_size=0, random_state=42, save_split_prefix=None):
    """Split data into train and test sets.
    Args:
        X (:py:class:`pandas.DataFrame` or :py:class:`numpy.Array`): Features to be split
        y (:py:class:`pandas.Series` or :py:class:`numpy.Array`): Target to be split
        train_size (`float`): Fraction of dataset to use for training. Default 1 (all data). Must be between 0 and 1.
        test_size (`float`): Fraction of dataset to use for testing. Default 0 (no data). Must be between 0 and 1.
        random_state (`int`): Integer value to choose random seed.
        save_split_prefix (str, optional): If given, the datasets will be saved with the given prefix, which can include
            the path to the directory for saving plus a prefix for the file, e.g. `data/features/2019-05-01-` will
            result in files saved to `data/features/2019-05-01-train-features.csv`,
            `data/features/2019-05-01-train-targets.csv`, and similar files for `test` and `validate` if `test_size`
            and/or `validate_size` are greater than 0.
    
    Returns:
        X (dict): Dictionary where keys are train, test and values are the X features for those splits.
        y (dict): Dictionary where keys are train, test and values are the y targets for those splits.
    """
    if y is not None:
        assert len(X) == len(y)
        include_y = True
    else:
        y = [0] * len(X)
        include_y = False
    if train_size + test_size == 1:
        prop = True
    elif train_size + test_size == len(X):
        prop = False
    else:
        raise ValueError("train_size + test_size"
                         "must equal 1 or equal the number of rows in the dataset")

    if prop:
        train_size = int(np.round(train_size * len(X)))
        test_size = int(len(X) - train_size)

    if train_size == 1:
        X_train, y_train = X, y
        X_test, y_test = [], []

    elif test_size == 0:
        X_train, y_train = X, y
        X_test, y_test = [], []

    else:
        X_train, X_test, y_train, y_test = sklearn.model_selection.train_test_split(X, y,
                                                                                    train_size=train_size,
                                                                                    random_state=random_state)
    # convert to a dictionary for future use
    X = dict(train=X_train)
    y = dict(train=y_train)

    if len(X_test) > 0:
        X["test"] = X_test
        y["test"] = y_test
    # save features and targets into test and train files with preferred prefix.
    if save_split_prefix is not None:
        for split in X:
            pd.DataFrame(X[split]).to_csv("%s-%s-features.csv" % (save_split_prefix, split), index=False)
            if include_y:
                pd.DataFrame(y[split]).to_csv("%s-%s-targets.csv" % (save_split_prefix, split), index=False)

            logger.info("X_%s and y_%s saved to %s-%s-features.csv and %s-%s-targets.csv",
                        split, split,
                        save_split_prefix, split,
                        save_split_prefix, split)

    if not include_y:
        y = dict(train=None)

    return X, y


def train_model(df, max_depth, n_estimators, learning_rate, random_state=None, method=None, save_tmo=None, **kwargs):
    """Function to train a specified model using features in given dataframe.
    Args:
        df (:py:class:`pandas.DataFrame`): DataFrame containing features.
        method (dict): Indicate type of model to train.
        save_tmo (str): Path to save the trained model.
        max_depth (int): How deep a tree can be.
        n_estimators (int): Number of boosted trees.
        learning_rate (int): Determines the speed of learning process.
        random_state (int): Random seed for reproducibility. Default to None.
        **kwargs: Should contain arguments for specific requirements of model.
    Returns:
        model ('sklearn.linear_model.logistic.LogisticRegression'): Logistic regression model trained.
    
    """

    assert method in methods.keys()  # `methods` defined at top of file, possible methods for training

    # If "get_target" in the config file under "train_model", will get the target data for supervised learning
    # Otherwise y = None and the model must be unsupervised.
    if "get_target" in kwargs:
        y = get_target(df, **kwargs["get_target"])
        # remove target column from dataframe
        if kwargs["get_target"]["target"] in df.columns:
            df = df.drop(labels=[kwargs["get_target"]["target"]], axis=1)
    else:
        y = None

    # If "choose_features" in the config file under "train_model", will reduce the feature set to those listed
    if "choose_features" in kwargs:
        X = choose_features(df, **kwargs["choose_features"])
    else:
        X = df

    # Splits the training data according to the "split_data" parameters. If this is an empty dictionary
    # (from prior step, because it is not in the configuration file), then the full dataset is returned (train_size=1)
    X, y = split_data(X, y, **kwargs["split_data"])

    # checks data types of feature inputs - has to be numeric or boolean for xgboost model
    for col in X["train"].columns:
        if X["train"][col].dtype not in [np.dtype('float64'), np.dtype('float32'), np.dtype('int64'), np.dtype('bool')]:
            raise ValueError('Input dataframe can only have numeric or boolean types!')
    
    if random_state is not None:
        pre_defined_kwargs = {'max_depth':max_depth, 'n_estimators':n_estimators,
                              "learning_rate":learning_rate, 'random_state':random_state}
    else:
        pre_defined_kwargs = {'max_depth':max_depth, 'n_estimators':n_estimators,
                              "learning_rate":learning_rate}
    # Instantiates a model class for the training `method` provided
    model = methods[method](**pre_defined_kwargs)

    # Fit the model with the training data and time doing so
    model.fit(X["train"], y["train"])

    # Save the trained model object
    if save_tmo is not None:
        with open(save_tmo, "wb") as f:
            pickle.dump(model, f)
        logger.info("Trained model object saved to %s", save_tmo)

    return model


def run_training(args):
    """Orchestrates the training of the model using command line arguments."""

    with open(args.config, "r") as f:
        config = yaml.load(f)

    logger.info("Training configuration file, %s, loaded", args.config)

    if args.input is not None:
        df = pd.read_csv(args.input)
        logger.info("Features for input into model loaded from %s", args.input)
    elif "generate_features" in config and "save_features" in config["generate_features"]:
        df = pd.read_csv(config["generate_features"]["save_features"])
        logger.info("Features for input into model loaded from %s", config["generate_features"]["save_features"])
    else:
        raise ValueError("Path to CSV for input data must be provided through --input or "
                         "'load_data' configuration must exist in config file")

    tmo = train_model(df, **config["train_model"])

    if args.output is not None:
        with open(args.output, "wb") as f:
            pickle.dump(tmo, f)
        logger.info("Trained model object saved to %s", args.output)

